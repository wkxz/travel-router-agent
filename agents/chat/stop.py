"""
Stop handler — EdgeOne Makers
========================================

The file path agents/chat/stop.py is auto-mapped to **POST /chat/stop**.
"""

from .._logger import create_logger

logger = create_logger("stop")


async def handler(context):
    """Abort the running agent for this conversation."""
    body = context.request.body or {}
    conversation_id = body.get('conversation_id')
    logger.log(f"conversation_id: {conversation_id}")

    if not conversation_id:
        logger.error('conversation_id is required')
        return {
            'status_code': 400,
            'body': {
                'status': 'error',
                'message': 'conversation_id is required',
            }
        }

    result = context.utils.abort_active_run(conversation_id)
    logger.log("abort_active_run result:", {
        "aborted": result.aborted,
        "conversation_id": result.conversation_id,
        "run_id": result.run_id,
    })

    return {
        "status": "aborting" if result.aborted else "idle",
        "conversationId": result.conversation_id or conversation_id,
        "runId": result.run_id,
        "aborted": result.aborted,
    }
