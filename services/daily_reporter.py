import logging
from google import genai
from core.config import settings
from services.notifications import NotificationManager
from services.anomaly_detector import scan_and_analyze_anomalies
from database import async_session_maker

logger = logging.getLogger(__name__)

async def _invoke_gemini(prompt: str) -> str:
    """Helper to invoke Gemini explicitly for business reporting."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        logger.error("GEMINI_API_KEY missing, cannot generate report.")
        return "无法生成: LLM API Key未配置。"
        
    try:
        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return response.text or "生成报告内容为空"
    except Exception as e:
        logger.error(f"Failed to generate AI report: {e}")
        return f"AI 摘要失败: {str(e)}"

async def generate_morning_briefing():
    """Generates the morning briefing using intraday anomaly data and broadcasts it."""
    logger.info("Executing Morning Briefing Task...")
    try:
        anomalies_data = []
        async with async_session_maker() as session:
            # We fetch 5 top anomalies 
            anomalies_data = await scan_and_analyze_anomalies(session, limit_count=5)
            
        if not anomalies_data:
            logger.info("No anomalies detected for Morning Briefing.")
            await NotificationManager.broadcast(
                title="🌅 Quantify 美股开盘速递",
                content="早盘扫描完成：当前市场暂无显著异动标的。"
            )
            return

        # Prepare summary list for prompt
        anomalies_text = []
        for anomaly in anomalies_data:
            anomalies_text.append(f"- {anomaly['ticker']} ({anomaly['company_name']}): 涨跌幅 {anomaly['price_change']}%")
        
        prompt = f"""
        你是一个对冲基金经理，现在是美股开盘时间。
        请根据以下开盘异动数据：
        {chr(10).join(anomalies_text)}
        
        写一份不超过 300 字的『美股开盘速递』晨会纪要。
        要求语言专业、精炼，直接指出核心驱动因素，使用 Markdown 格式渲染重点。
        """
        
        ai_report = await _invoke_gemini(prompt)
        
        await NotificationManager.broadcast(
            title="🌅 Quantify 美股开盘速递",
            content=ai_report
        )
            
    except Exception as e:
        logger.error(f"Error executing Morning Briefing: {e}")

async def generate_post_market_summary():
    """Generates the post market summary and broadcasts it."""
    logger.info("Executing Post Market Summary Task...")
    try:
        anomalies_data = []
        async with async_session_maker() as session:
            # For post market, we might also just rely on anomaly logic or a different scanner
            anomalies_data = await scan_and_analyze_anomalies(session, limit_count=10)
            
        if not anomalies_data:
            logger.info("No anomalies detected for Post Market Summary.")
            await NotificationManager.broadcast(
                title="🌃 Quantify 美股盘后总结",
                content="盘后扫描完成：今日市场平稳收盘，暂无特大级别异动。"
            )
            return

        # Prepare summary list for prompt
        anomalies_text = []
        for anomaly in anomalies_data:
            anomalies_text.append(f"- {anomaly['ticker']} ({anomaly['company_name']}): 涨跌幅 {anomaly['price_change']}%")
        
        prompt = f"""
        你是一个华尔街顶级量化策略师，现在是美股收盘之后。
        请针对以下今日全天涨跌幅极值标的的数据集：
        {chr(10).join(anomalies_text)}
        
        写一份『美股盘后总结』复盘纪要（不超过 400 字）。
        要求深入浅出，判断今日市场情绪走向，并用 Markdown 格式渲染重点标的。
        """
        
        ai_report = await _invoke_gemini(prompt)
        
        await NotificationManager.broadcast(
            title="🌃 Quantify 美股盘后总结",
            content=ai_report
        )
            
    except Exception as e:
        logger.error(f"Error executing Post Market Summary: {e}")
