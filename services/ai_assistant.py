import os
from google import genai
import logging

from core.config import settings

logger = logging.getLogger(__name__)

async def generate_stock_report(ticker: str, analysis_data: dict) -> str:
    """
    Generates a concise markdown investment report using Gemini 1.5 Flash.
    Initializes the client PER REQUEST to ensure concurrency safety and config isolation.
    """
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        logger.error("GEMINI_API_KEY environment variable is missing.")
        yield "Error: LLM API key not configured. Cannot generate report."
        return

    try:
        # 每次调用时独立初始化 Client
        client = genai.Client(api_key=api_key)
        
        profile = analysis_data.get('profile', {})
        valuation = analysis_data.get('valuation_metrics', {})
        factors = valuation.get('factor_scores', {})
        
        company_name = profile.get('Name', ticker)
        industry = profile.get('Industry', 'Unknown')
        
        ttm = valuation.get('ttm', {})

        prompt = f"""
        你是一位华尔街资深量化分析师。请根据以下我提供的美股最新基本面与多因子打分数据，为 {company_name} ({ticker}) 撰写一份专业的投资简报。
        
        【数据总览】
        - 行业: {industry}
        - 最新股价: ${valuation.get('valuation', {}).get('current_price', 'N/A')}
        - DCF 内在价值估算: ${valuation.get('valuation', {}).get('dcf_intrinsic_value_per_share', 'N/A')}
        - 估值安全边际: {valuation.get('valuation', {}).get('margin_of_safety', 0) * 100:.1f}%
        
        【最新基本面 (TTM 滚动十二个月)】
        - 营收 (Revenue): ${ttm.get('revenue', 'N/A')}
        - 净利润 (Net Income): ${ttm.get('net_income', 'N/A')}
        - 自由现金流 (FCF): ${ttm.get('free_cash_flow', 'N/A')}
        - 净资产收益率 (ROE): {ttm.get('roe', 0) * 100:.2f}%
        
        【多因子得分 (0-100)】
        - 价值 (Value): {factors.get('value', 'N/A')}
        - 质量 (Quality): {factors.get('quality', 'N/A')}
        - 成长 (Growth): {factors.get('growth', 'N/A')}
        - 健康 (Health): {factors.get('health', 'N/A')}
        - 动量 (Momentum): {factors.get('momentum', 'N/A')}
        
        【要求】
        1. 使用 Markdown 格式排版。
        2. 全文字数控制在 500 字左右，言简意赅。
        3. 必须包含以下四个标准模块：
           - **核心观点**: 一段话总结该股目前的投资吸引力。
           - **估值诊断**: 结合 DCF 和安全边际，评判当前股价是否被低估或高估。
           - **因子解读**: 挑出得分最高和最低的因子进行专业点评，无需罗列所有分数。
           - **潜在风险**: 基于行业或低分因子，指出可能面临的风险。
        4. 语气专业、客观，避免强烈的买卖推荐。
        """

        response_stream = await client.aio.models.generate_content_stream(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        async for chunk in response_stream:
            if chunk.text:
                yield chunk.text
        
    except Exception as e:
        logger.error(f"Error generating report for {ticker}: {e}")
        yield f"Error generating report: {str(e)}"

async def generate_anomaly_attribution(ticker: str, price_change: float, news_list: list) -> str:
    """
    Generates a concise attribution report for a stock price anomaly using Gemini 1.5 Flash.
    Initializes the client PER REQUEST to ensure concurrency safety.
    """
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        logger.error("GEMINI_API_KEY environment variable is missing.")
        return "Error: LLM API key not configured."

    try:
        # 每次调用时独立初始化 Client
        client = genai.Client(api_key=api_key)
        
        prompt = f"""
        你是一个华尔街量化分析师。今日开盘，{ticker} 股价异动，涨跌幅为 {price_change}%。
        以下是过去 24 小时的相关新闻摘要：
        {news_list}
        
        请严格根据这些新闻，分析导致该股票异动的最核心原因。
        如果新闻中没有明确原因，请回复‘缺乏明确新闻催化剂，可能为资金面或技术面行为’。
        输出要求专业、简洁，不超过 150 字。
        """

        response = await client.aio.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        return response.text or "无法生成归因分析"
        
    except Exception as e:
        logger.error(f"Error generating anomaly attribution for {ticker}: {e}")
        return f"Error generation attribution: {str(e)}"
