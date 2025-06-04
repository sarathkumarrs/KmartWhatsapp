import os
import json
import requests
from flask import Flask, request, render_template, jsonify
from dotenv import load_dotenv
import datetime # For timestamps

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configuration from environment variables
WHATSAPP_API_VERSION = os.getenv('WHATSAPP_API_VERSION', 'v19.0')
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
WHATSAPP_ACCESS_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN')
WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN')

# Basic in-memory store for messages (NOT FOR PRODUCTION - use a database)
# Each message will be a dictionary: {'id': 'wa_message_id', 'sender': 'phone_number', 'text': 'message_content', 'direction': 'in/out', 'timestamp': 'iso_timestamp'}
MESSAGES_STORE = []


@app.route('/')
def index():
    """Serves the main chat UI."""
    return render_template('chat_ui.html')


@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """Handles incoming WhatsApp messages and events."""
    if request.method == 'GET':
        # Webhook verification
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')

        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return challenge, 200
        else:
            print("VERIFICATION_FAILED")
            return "Verification failed", 403

    elif request.method == 'POST':
        data = request.get_json()
        print(f"Received webhook data: {json.dumps(data, indent=2)}")

        if data.get('object') == 'whatsapp_business_account':
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    if change.get('field') == 'messages':
                        value = change.get('value', {})
                        # Handle incoming messages
                        if 'messages' in value:
                            for message_data in value.get('messages', []):
                                if message_data.get('type') == 'text':
                                    sender_id = message_data.get('from')
                                    text_body = message_data.get('text', {}).get('body')
                                    message_id = message_data.get('id')
                                    timestamp = datetime.datetime.now().isoformat()

                                    if sender_id and text_body:
                                        MESSAGES_STORE.append({
                                            'id': message_id,
                                            'sender': sender_id,
                                            'text': text_body,
                                            'direction': 'in',
                                            'timestamp': timestamp
                                        })
                                        print(f"Incoming message from {sender_id}: {text_body}")
                        # Handle message status updates (sent, delivered, read)
                        elif 'statuses' in value:
                            for status_data in value.get('statuses', []):
                                message_id = status_data.get('id')
                                status = status_data.get('status')
                                recipient_id = status_data.get('recipient_id')
                                timestamp_s = status_data.get('timestamp') # WhatsApp timestamp is string unix epoch
                                timestamp_dt = datetime.datetime.fromtimestamp(int(timestamp_s)).isoformat() if timestamp_s else datetime.datetime.now().isoformat()

                                print(f"Status update for message {message_id} to {recipient_id}: {status}")
                                # You can update your MESSAGES_STORE here if you track outgoing message status
                                for msg in MESSAGES_STORE:
                                    if msg.get('id_internal_for_status') == message_id and msg['direction'] == 'out': # Assuming you store an internal ID for correlating status
                                        msg['status'] = status
                                        msg['timestamp_status_update'] = timestamp_dt
                                        break
        return "EVENT_RECEIVED", 200
    else:
        return "Method Not Allowed", 405


@app.route('/send_message', methods=['POST'])
def send_message_route():
    """Endpoint to send a message from the custom UI."""
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

    try:
        response = requests.post(whatsapp_api_url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        response_data = response.json()
        message_id_wa = response_data.get("messages", [{}])[0].get("id")

        # Store outgoing message
        MESSAGES_STORE.append({
            'id': message_id_wa, # WhatsApp's message ID for the sent message
            'id_internal_for_status': message_id_wa, # Use this to correlate status updates
            'sender': WHATSAPP_PHONE_NUMBER_ID, # Your business number
            'recipient': recipient_wa_id,
            'text': message_text,
            'direction': 'out',
            'timestamp': datetime.datetime.now().isoformat(),
            'status': 'sent' # Initial status, will be updated by webhook
        })
        print(f"Message sent to {recipient_wa_id}: {message_text}. Response: {response_data}")
        return jsonify({'status': 'success', 'data': response_data, 'message_id': message_id_wa})
    except requests.exceptions.RequestException as e:
        error_message = f"Error sending WhatsApp message: {e}"
        if response is not None:
            error_message += f" | Response: {response.text}"
        print(error_message)
        return jsonify({'status': 'error', 'message': error_message}), 500
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({'status': 'error', 'message': 'An unexpected error occurred.'}), 500


@app.route('/get_messages', methods=['GET'])
def get_messages_route():
    """Endpoint to fetch all messages for the UI."""
    # In a real app, you'd filter by conversation/user
    return jsonify(sorted(MESSAGES_STORE, key=lambda x: x['timestamp']))

if __name__ == '__main__':
    if not all([WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ACCESS_TOKEN, WHATSAPP_VERIFY_TOKEN]):
        print("ERROR: WhatsApp API credentials are not fully configured. Please check your .env file.")
    else:
        print("WhatsApp credentials seem to be loaded.")
        print(f"Webhook will be available at /webhook")
        port = int(os.environ.get("PORT", 5000))
        print(f"UI will be available at http://127.0.0.1:{port}/")
    app.run(debug=os.getenv('FLASK_DEBUG', 'False').lower() == 'true', host='0.0.0.0', port=port)

