import os
import json
import requests
from flask import Flask, request, render_template, jsonify
from dotenv import load_dotenv
import datetime
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configuration from environment variables
WHATSAPP_API_VERSION = os.getenv('WHATSAPP_API_VERSION', 'v19.0')
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
WHATSAPP_ACCESS_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN')
WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN')

# Basic in-memory store for messages (NOT FOR PRODUCTION - use a database)
MESSAGES_STORE = []

@app.route('/')
def index():
    """Serves the main chat UI."""
    return render_template('chat_ui.html')

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.datetime.now().isoformat(),
        'config_check': {
            'phone_number_id': bool(WHATSAPP_PHONE_NUMBER_ID),
            'access_token': bool(WHATSAPP_ACCESS_TOKEN),
            'verify_token': bool(WHATSAPP_VERIFY_TOKEN)
        }
    })

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """Handles incoming WhatsApp messages and events."""
    if request.method == 'GET':
        # Webhook verification
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        logger.info(f"Webhook verification attempt: mode={mode}, token_match={token == WHATSAPP_VERIFY_TOKEN}")
        
        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            logger.info("WEBHOOK_VERIFIED")
            return challenge, 200
        else:
            logger.error("VERIFICATION_FAILED")
            return "Verification failed", 403

    elif request.method == 'POST':
        try:
            data = request.get_json()
            logger.info(f"Received webhook data: {json.dumps(data, indent=2)}")

            if data.get('object') == 'whatsapp_business_account':
                for entry in data.get('entry', []):
                    for change in entry.get('changes', []):
                        if change.get('field') == 'messages':
                            value = change.get('value', {})
                            
                            # Handle incoming messages
                            if 'messages' in value:
                                for message_data in value.get('messages', []):
                                    logger.info(f"Processing message: {message_data}")
                                    
                                    message_type = message_data.get('type')
                                    sender_id = message_data.get('from')
                                    message_id = message_data.get('id')
                                    timestamp = datetime.datetime.now().isoformat()
                                    
                                    text_body = None
                                    
                                    # Handle different message types
                                    if message_type == 'text':
                                        text_body = message_data.get('text', {}).get('body')
                                    elif message_type == 'image':
                                        text_body = f"[Image] {message_data.get('image', {}).get('caption', 'No caption')}"
                                    elif message_type == 'document':
                                        text_body = f"[Document] {message_data.get('document', {}).get('filename', 'Unknown file')}"
                                    elif message_type == 'audio':
                                        text_body = "[Audio message]"
                                    elif message_type == 'video':
                                        text_body = f"[Video] {message_data.get('video', {}).get('caption', 'No caption')}"
                                    else:
                                        text_body = f"[{message_type.upper()} message]"
                                    
                                    if sender_id and text_body:
                                        message_obj = {
                                            'id': message_id,
                                            'sender': sender_id,
                                            'text': text_body,
                                            'direction': 'in',
                                            'timestamp': timestamp,
                                            'type': message_type
                                        }
                                        MESSAGES_STORE.append(message_obj)
                                        logger.info(f"Stored incoming message: {message_obj}")
                            
                            # Handle message status updates
                            elif 'statuses' in value:
                                for status_data in value.get('statuses', []):
                                    message_id = status_data.get('id')
                                    status = status_data.get('status')
                                    recipient_id = status_data.get('recipient_id')
                                    timestamp_s = status_data.get('timestamp')
                                    
                                    logger.info(f"Status update: message_id={message_id}, status={status}, recipient={recipient_id}")
                                    
                                    # Update message status in store
                                    for msg in MESSAGES_STORE:
                                        if msg.get('id') == message_id and msg['direction'] == 'out':
                                            msg['status'] = status
                                            if timestamp_s:
                                                msg['timestamp_status_update'] = datetime.datetime.fromtimestamp(int(timestamp_s)).isoformat()
                                            break
            
            return "EVENT_RECEIVED", 200
            
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            return "ERROR", 500
    
    else:
        return "Method Not Allowed", 405

@app.route('/send_message', methods=['POST'])
def send_message_route():
    """Endpoint to send a message from the custom UI."""
    try:
        data = request.get_json()
        recipient_wa_id = data.get('recipient_wa_id')
        message_text = data.get('message_text')

        if not recipient_wa_id or not message_text:
            return jsonify({'status': 'error', 'message': 'Recipient ID and message text are required.'}), 400

        whatsapp_api_url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient_wa_id,
            "type": "text",
            "text": {"body": message_text}
        }

        logger.info(f"Sending message to {recipient_wa_id}: {message_text}")
        
        response = requests.post(whatsapp_api_url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        response_data = response.json()
        message_id_wa = response_data.get("messages", [{}])[0].get("id")

        # Store outgoing message
        message_obj = {
            'id': message_id_wa,
            'sender': WHATSAPP_PHONE_NUMBER_ID,
            'recipient': recipient_wa_id,
            'text': message_text,
            'direction': 'out',
            'timestamp': datetime.datetime.now().isoformat(),
            'status': 'sent'
        }
        MESSAGES_STORE.append(message_obj)
        
        logger.info(f"Message sent successfully: {message_obj}")
        return jsonify({'status': 'success', 'data': response_data, 'message_id': message_id_wa})
        
    except requests.exceptions.RequestException as e:
        error_message = f"Error sending WhatsApp message: {e}"
        logger.error(error_message)
        return jsonify({'status': 'error', 'message': error_message}), 500
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({'status': 'error', 'message': 'An unexpected error occurred.'}), 500

@app.route('/get_messages', methods=['GET'])
def get_messages_route():
    """Endpoint to fetch all messages for the UI."""
    logger.info(f"Fetching {len(MESSAGES_STORE)} messages")
    return jsonify({
        'messages': sorted(MESSAGES_STORE, key=lambda x: x['timestamp']),
        'count': len(MESSAGES_STORE)
    })

@app.route('/debug/messages')
def debug_messages():
    """Debug endpoint to see all stored messages."""
    return jsonify({
        'total_messages': len(MESSAGES_STORE),
        'messages': MESSAGES_STORE,
        'timestamp': datetime.datetime.now().isoformat()
    })

if __name__ == '__main__':
    # Check configuration
    missing_configs = []
    if not WHATSAPP_PHONE_NUMBER_ID:
        missing_configs.append('WHATSAPP_PHONE_NUMBER_ID')
    if not WHATSAPP_ACCESS_TOKEN:
        missing_configs.append('WHATSAPP_ACCESS_TOKEN')
    if not WHATSAPP_VERIFY_TOKEN:
        missing_configs.append('WHATSAPP_VERIFY_TOKEN')
    
    if missing_configs:
        logger.error(f"Missing configuration: {', '.join(missing_configs)}")
    else:
        logger.info("All WhatsApp credentials loaded successfully")
    
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting server on port {port}")
    logger.info(f"Health check: https://kmartwhatsapp.onrender.com/health")
    logger.info(f"UI: https://kmartwhatsapp.onrender.com/")
    logger.info(f"Debug messages: https://kmartwhatsapp.onrender.com/debug/messages")
    
    app.run(debug=os.getenv('FLASK_DEBUG', 'False').lower() == 'true', host='0.0.0.0', port=port)