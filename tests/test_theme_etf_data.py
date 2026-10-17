"""The real get_etf_data adapter, exercised through the HTTP layer (membership tests fake it)."""

import httpx
import pytest

from services import eodhd_client
from services.themes.membership import parse_etf_holdings


CCJ_ROW = {"Code": "CCJ", "Exchange": "US", "Name": "Cameco", "Assets_%": "22.5"}
NXE_ROW = {"Code": "NXE", "Exchange": "US", "Name": "NexGen", "Assets_%": 7.1}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            {"Holdings": {"CCJ.US": CCJ_ROW, "BAD": "x"}, "Inception_Date": "2010-11-04"},
            {"holdings": [{**CCJ_ROW, "_key": "CCJ.US"}], "inception_date": "2010-11-04"},
            id="dict-keyed-by-symbol",
        ),
        pytest.param(
            {"Holdings": [CCJ_ROW, "junk", None, 3, NXE_ROW], "Inception_Date": "2010-11-04"},
            {"holdings": [CCJ_ROW, NXE_ROW], "inception_date": "2010-11-04"},
            id="list-with-junk",
        ),
        pytest.param(
            {"Inception_Date": "2010-11-04"},
            {"holdings": [], "inception_date": "2010-11-04"},
            id="holdings-missing",
        ),
        pytest.param({"Holdings": "CCJ.US", "Inception_Date": "2010-11-04"}, None, id="holdings-wrong-type"),
        pytest.param(
            {"Holdings": {"CCJ.US": CCJ_ROW}, "Inception_Date": ""},
            {"holdings": [{**CCJ_ROW, "_key": "CCJ.US"}], "inception_date": None},
            id="inception-blank",
        ),
        pytest.param(
            {"Holdings": [], "Inception_Date": 20101104},
            {"holdings": [], "inception_date": None},
            id="inception-non-string",
        ),
        pytest.param([{"Holdings": {"CCJ.US": CCJ_ROW}}], None, id="top-level-list"),
    ],
)
async def test_get_etf_data_normalises_provider_payload(monkeypatch, payload, expected):
    monkeypatch.setattr(eodhd_client, "EODHD_API_KEY", "sentinel-token")
    monkeypatch.setattr(eodhd_client, "_quota_exhausted_on", None)
    monkeypatch.setattr(eodhd_client, "MAX_RETRIES", 1)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await eodhd_client.get_etf_data("URA.US", client=client)

    assert len(requests) == 1
    assert requests[0].url.path.endswith("/fundamentals/URA.US")
    assert requests[0].url.params.get("filter") == "ETF_Data"
    assert result == expected


async def test_get_etf_data_output_feeds_parse_etf_holdings(monkeypatch):
    monkeypatch.setattr(eodhd_client, "EODHD_API_KEY", "sentinel-token")
    monkeypatch.setattr(eodhd_client, "_quota_exhausted_on", None)
    monkeypatch.setattr(eodhd_client, "MAX_RETRIES", 1)

    def handler(request):
        return httpx.Response(200, json={"Holdings": {"CCJ.US": CCJ_ROW, "BAD": "x"}, "Inception_Date": "2010-11-04"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await eodhd_client.get_etf_data("URA.US", client=client)

    holdings = parse_etf_holdings("URA", payload)
    assert [(h.holding_key, h.weight_pct, h.holding_name) for h in holdings] == [("CCJ.US", 22.5, "Cameco")]
