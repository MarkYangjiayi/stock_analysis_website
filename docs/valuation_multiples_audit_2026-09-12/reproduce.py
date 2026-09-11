import gzip
import os
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/valuation_audit_2026-09-12'
NAMES = ['MSFT','JPM','XOM','JNJ','WMT','CAT','NEE','AMT','AMZN','INTC']
ANCHOR = os.environ.get('AUDIT_ANCHOR', '2025-03-31')
REVENUE = {
    'MSFT':'RevenueFromContractWithCustomerExcludingAssessedTax',
    'JPM':'RevenuesNetOfInterestExpense', 'XOM':'Revenues',
    'JNJ':'RevenueFromContractWithCustomerExcludingAssessedTax', 'WMT':'Revenues',
    'CAT':'Revenues', 'NEE':'RegulatedAndUnregulatedOperatingRevenue', 'AMT':'Revenues',
    'AMZN':'RevenueFromContractWithCustomerExcludingAssessedTax',
    'INTC':'RevenueFromContractWithCustomerExcludingAssessedTax',
}

def day(s): return date.fromisoformat(s)
def load(path):
    if str(path).endswith('.gz'):
        with gzip.open(path,'rt') as f: return json.load(f)
    return json.loads(path.read_text())

def facts(sec, tag, unit='USD'):
    return [r for r in sec['facts']['us-gaap'].get(tag,{}).get('units',{}).get(unit,[])
            if r.get('form') in ['10-K','10-Q','10-K/A','10-Q/A'] and r.get('filed','9999') < ANCHOR]

def pick(sec, tag, end, start=None, unit='USD'):
    rows=[r for r in facts(sec,tag,unit) if abs((day(r['end'])-day(end)).days)<=4
          and ((start is None and 'start' not in r) or
               (start is not None and 'start' in r and abs((day(r['start'])-day(start)).days)<=4))]
    if not rows: return None
    return min(rows,key=lambda r:(abs((day(r['end'])-day(end)).days), -(day(r['filed']).toordinal())))

def ttm(sec, tag, end, unit='USD'):
    annual=[r for r in facts(sec,tag,unit) if 'start' in r and 330<=(day(r['end'])-day(r['start'])).days<=380
            and day(r['end'])<=day(end)+timedelta(days=4) and (day(end)-day(r['end'])).days<365]
    if not annual:return None
    base=max(annual,key=lambda r:(r['end'],r['filed']))
    if abs((day(base['end'])-day(end)).days)<=4:return {'value':base['val'],'facts':[base],'tag':tag}
    start=(day(base['end'])+timedelta(days=1)).isoformat()
    prev_end=day(end).replace(year=day(end).year-1).isoformat()
    current=pick(sec,tag,end,start,unit);prior=pick(sec,tag,prev_end,base['start'],unit)
    if current is None or prior is None:return None
    return {'value':base['val']+current['val']-prior['val'],'facts':[base,current,prior],'tag':tag}

def instant(sec,tags,end,unit='USD'):
    for tag in tags:
        fact=pick(sec,tag,end,unit=unit)
        if fact:return {'value':fact['val'],'facts':[fact],'tag':tag}
    return None

def flow(sec,tags,end,unit='USD'):
    for tag in tags:
        found=ttm(sec,tag,end,unit)
        if found:return found
    return None

def val(v): return v['value'] if v else None
def ratio(a,b):return a/b if a is not None and b is not None and a>0 and b>0 else None

results=[]
for name in NAMES:
    live=load(OUT/f'{name}-live.json');h=live['valuation_history'];points=h['points']
    point=next(p for p in points if p['date']==ANCHOR)
    if point['basis_id'] is None:
        print(name,'not verified yet');continue
    basis=next(b for b in h['bases'] if b['id']==point['basis_id']);end=basis['period_end']
    sec=load(OUT/'XOM-sec.json.gz' if name=='XOM' and ANCHOR == '2025-03-31' else ROOT/f'data/sec_mapping_cache/sec/{name}.json.gz')
    if ANCHOR != '2025-03-31' and (OUT/f'{name}-sec-fresh.json.gz').exists():sec=load(OUT/f'{name}-sec-fresh.json.gz')
    price=load(OUT/(f'{name}-yahoo.json' if ANCHOR == '2025-03-31' else f'{name}-yahoo-{ANCHOR}.json'))['chart']['result'][0]['indicators']['quote'][0]['close'][0]
    inputs={
        'eps':flow(sec,['EarningsPerShareDiluted'],end,'USD/shares'),
        'revenue':flow(sec,[REVENUE[name]],end),
        'cfo':flow(sec,['NetCashProvidedByUsedInOperatingActivities'],end),
        'capex':flow(sec,['PaymentsToAcquirePropertyPlantAndEquipment','PaymentsForAdditionsToPropertyPlantAndEquipment','PaymentsToAcquireProductiveAssets'],end),
        'book':instant(sec,['StockholdersEquity'],end),
        'shares':instant(sec,['CommonStockSharesOutstanding'],end,'shares'),
        'debt_total':instant(sec,['LongTermDebtAndFinanceLeaseObligationsCurrentAndNoncurrent','DebtAndFinanceLeaseObligationsCurrentAndNoncurrent','LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities'],end),
        'cash':instant(sec,['CashCashEquivalentsAndShortTermInvestments','CashAndCashEquivalentsAtCarryingValue'],end),
        'op_income':flow(sec,['OperatingIncomeLoss'],end),
        'da':flow(sec,['DepreciationDepletionAndAmortization','DepreciationAmortizationAndAccretionNet','DepreciationAndAmortization','DepreciationDepletionAndAmortizationPropertyPlantAndEquipment'],end),
    }
    # CAT total capex also includes purchases of equipment leased to others.
    if name == 'CAT':
        leased = flow(sec,['PaymentsToAcquireEquipmentOnLease'],end)
        inputs['capex'] = {'value':inputs['capex']['value']+leased['value'],
                           'facts':inputs['capex']['facts']+leased['facts'],
                           'tag':'PaymentsToAcquirePropertyPlantAndEquipment + PaymentsToAcquireEquipmentOnLease'}
        total_equity = instant(sec,['StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],end)
        minority = instant(sec,['MinorityInterest'],end)
        if total_equity and minority:
            inputs['book'] = {'value':total_equity['value']-minority['value'],
                              'facts':total_equity['facts']+minority['facts'],
                              'tag':'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest - MinorityInterest'}
    if name == 'NEE' and end == '2024-12-31':
        inputs['capex']={'value':24729000000,'facts':[], 'tag':'Manual consolidated cash-flow statement: FPL 7,992 + NEER 16,215 + nuclear fuel 399 + other 123 (USD million)',
                        'url':'https://www.sec.gov/Archives/edgar/data/753308/000075330825000011/nee-20241231.htm'}
    v={k:val(x) for k,x in inputs.items()};v['fcf']=v['cfo']-abs(v['capex']) if v['cfo'] is not None and v['capex'] is not None else None
    # Two comparisons distinguish statement mapping from provider share proxy.
    equity=price*v['shares'] if v['shares'] is not None else None
    ref={'pe':ratio(price,v['eps']), 'ps':ratio(equity,v['revenue']), 'pb':ratio(equity,v['book']), 'pfcf':ratio(equity,v['fcf'])}
    same_shares=price*basis['inputs']['shares']
    controlled={k:ratio(same_shares,v[f]) for k,f in [('ps','revenue'),('pb','book'),('pfcf','fcf')]}
    implied=next((point['values'][k]*basis['inputs'][d]/(1 if k=='pe' else basis['inputs']['shares']) for k,d in [('pe','eps'),('ps','revenue'),('pb','book')] if point['values'][k] is not None),None)
    jumps=[];quotes={p['date']:p for p in live['historical_data']}
    for prev,p in zip(points,points[1:]):
        if p['date']<'2020-01-01' or prev['basis_id']!=p['basis_id']:continue
        a,b=quotes.get(prev['date'],{}).get('close'),quotes.get(p['date'],{}).get('close')
        if not a or not b:continue
        for k in ['pe','ps','pb','pfcf']:
            x,y=prev['values'][k],p['values'][k]
            if x and y and abs((y/x)/(b/a)-1)>0.1:jumps.append({'date':p['date'],'key':k,'multiple_change':y/x,'price_change':b/a})
    result={'ticker':name,'sector':live['profile']['sector'],'anchor':ANCHOR,'period_end':end,'available_from':basis['available_from'],
            'split_verified':h['split_history_verified'],'split_snapshot':h['split_history_snapshot_id'],
            'price_yahoo':price,'price_implied':implied,'online':point['values'],'sec_values':v,'sec_ratios':ref,
            'same_share_ratios':controlled,'online_inputs':basis['inputs'],'sec_evidence':inputs,
            'sec_cik':sec['cik'],'history_start':points[0]['price_date'],'history_end':points[-1]['price_date'],
            'same_basis_jumps':jumps,'latest_metrics':h['metrics']}
    results.append(result)
    print(name,end,'price',round(price,3),'ours', {k:round(x,3) if x is not None else None for k,x in point['values'].items()},'SEC', {k:round(x,3) if x is not None else None for k,x in ref.items()})
    print(' inputs SEC',v,'provider',basis['inputs'],'jumps',len(jumps))
(OUT/('comparison.json' if ANCHOR == '2025-03-31' else f'comparison-{ANCHOR}.json')).write_text(json.dumps(results,indent=2))
