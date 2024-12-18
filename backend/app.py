import os
import openai
from flask import Flask, request, jsonify
from google.cloud import storage, firestore
from PyPDF2 import PdfReader
import firebase_admin
from firebase_admin import credentials
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime
import re
import json
from werkzeug.datastructures import FileStorage

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Apply CORS to specific routes
CORS(app)

# Load Firebase Admin SDK
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred, {
    "storageBucket": "bindrproject.firebasestorage.app"
})

# Initialize Firestore client
db = firestore.Client.from_service_account_json("firebase-key.json")

# Get Firebase storage bucket
storage_client = storage.Client.from_service_account_json("firebase-key.json")
bucket = storage_client.bucket("bindrproject.firebasestorage.app")

# Initialize OpenAI API
client = openai.OpenAI(api_key = os.getenv("OPENAI_API_KEY"))
print(os.getenv("OPENAI_API_KEY"));

@app.before_request
def log_request():
    print(f"Incoming {request.method} request to {request.path}")

# Route: Ask GPT-4 a question
@app.route('/ask', methods=['POST'])
def ask_gpt():
    data = request.get_json()
    question = data.get('question', "")
    filename = data.get('filename', "")  # Filename associated with the question

    if not question.strip():
        return jsonify({"error": "No question provided."}), 400

    try:
        # Retrieve file content from Firestore if filename is provided
        file_content = ""
        if filename:
            doc_ref = db.collection("documents").document(filename)
            doc = doc_ref.get()
            if doc.exists:
                file_content = doc.to_dict().get("content", "")
            else:
                return jsonify({"error": f"No document found for filename: {filename}"}), 404

        
        prompt = (
            "If the user greets you, respond with a greeting and explain your role only once. "
            "Analyze the file content and answer the question based on what you learn. "
            "If the answer cannot be determined from the document, respond with 'Hm...I can't seem to find the answer to that in your document. '\n\n"
            f"Document Content:\n{file_content}\n\n"
            f"Question: {question}"
        )

        # Call GPT-4 API
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an assistant that helps the user answer questions based on the provided document."},
                {"role": "user", "content": prompt}
            ]
        )
        answer = response.choices[0].message.content
        return jsonify({"response": answer}), 200

    except Exception as e:
        print(f"Error while calling GPT-4o: {e}")
        return jsonify({"error": f"An error occurred while contacting GPT-4: {str(e)}"}), 500


@app.route('/createstudyplan', methods=['POST'])
def create_study_plan():
    try:
        # Extract form data (defaults to None or empty string if not provided)
        file_name = request.form.get('fileName', None)
        availability = request.form.get('availability', None)
        print(availability)
        overall_start = request.form.get('overallStart', None)
        overall_end = request.form.get('overallEnd', None)
        topics = request.form.get('topics', None)
        study_preference = request.form.get('studyPreference', None)

        
        file_content = ""
        if file_name:
            doc_ref = db.collection("documents").document(file_name)
            doc = doc_ref.get()
            if doc.exists:
                file_content = doc.to_dict().get("content", "")
            else:
                return jsonify({"error": f"No document found for filename: {file_name}"}), 404

        
        prompt = "Based on the following user preferences, generate a detailed and personalized study plan:"

        if availability:
            prompt += (
                f"\n1. Weekly availability: {availability}."
                f"\n   Note: If the user has selected a larger time block than will take to study a given day's topics, include a time estimate."
            )
        if overall_start and overall_end:
            prompt += f"\n2. Study timeline: Start - {overall_start}, End - {overall_end}"
        if topics:
            prompt += f"\n3. Specific topics to cover: {topics}"
        if study_preference:
            prompt += f"\n4. Preferred study method: {study_preference}"
        
        if file_content:
            file_content = file_content
            prompt += f"\n\nUse the following file to extract due dates, topics to cover and resources need:\n{file_content}"

      
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant specializing in study plans."},
                {"role": "user", "content": prompt}
            ]
        )

        
        study_plan = response.choices[0].message.content
       
        return jsonify({"study_plan": study_plan}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    

# Route: Upload a file and extract its text
@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No selected file"}), 400

    # Validate file type (PDF only)
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "Only PDF files are allowed."}), 400

    try:
        # Upload file to Firebase Storage
        blob = bucket.blob(file.filename)
        blob.upload_from_file(file)
        blob.make_public()

        # Extract text from PDF
        file.seek(0)  # Reset file pointer
        reader = PdfReader(file)
        extracted_text = " ".join([page.extract_text() or "" for page in reader.pages])

        # Store extracted text in Firestore
        doc_ref = db.collection("documents").document(file.filename)
        doc_ref.set({
            "filename": file.filename,
            "url": blob.public_url,
            "content": extracted_text
        })

        return jsonify({
            "message": "File uploaded and processed successfully",
            "filename": file.filename,
            "url": blob.public_url,
            "extracted_text_preview": extracted_text[:500]  # Send a preview of the text
        }), 200
    except Exception as e:
        print(f"Error occurred during file upload: {e}")
        return jsonify({"error": f"Failed to process the file: {str(e)}"}), 500


# Route: Search documents by keyword
@app.route("/search", methods=["GET"])
def search_documents():
    query = request.args.get("q", "")
    if not query.strip():
        return jsonify({"error": "Query parameter 'q' is required."}), 400

    try:
        # Search Firestore for matching documents
        docs = db.collection("documents").stream()
        results = []
        for doc in docs:
            data = doc.to_dict()
            if query.lower() in data.get("content", "").lower():
                snippet = data["content"][:500]  # Return a snippet
                results.append({
                    "filename": data["filename"],
                    "url": data["url"],
                    "snippet": snippet
                })

        return jsonify({"results": results}), 200
    except Exception as e:
        print(f"Error occurred during search: {e}")
        return jsonify({"error": f"Failed to search documents: {str(e)}"}), 500

@app.route("/extract-dates", methods=["POST"])
def extract_dates():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No selected file"}), 400

    # Validate file type (PDF only)
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "Only PDF files are allowed."}), 400

    try:
        # Extract text from PDF
        file.seek(0)  # Reset file pointer
        reader = PdfReader(file)
        extracted_text = " ".join([page.extract_text() or "" for page in reader.pages])

        # Regex to extract dates (format: MM/DD/YYYY, Month Day, YYYY, etc.)
        date_pattern = r"(\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,\s+\d{4})?)"
        found_dates = re.findall(date_pattern, extracted_text)

        # Add logic to associate dates with events (basic example)
        events = []
        for date in found_dates:
            events.append({"date": date, "event": "Event description"})  # Placeholder for event text extraction

        return jsonify({"dates": events}), 200
    except Exception as e:
        print(f"Error extracting dates: {e}")
        return jsonify({"error": f"Failed to extract dates: {str(e)}"}), 500


@app.route("/list-files", methods=["GET"])
def list_files():
    try:
        # Get all blobs (files) in the bucket
        blobs = bucket.list_blobs()

        # Prepare a list of file details
        files = [{"name": blob.name, "url": blob.public_url} for blob in blobs]

        return jsonify({"files": files}), 200
    except Exception as e:
        print(f"Error occurred while listing files: {e}")
        return jsonify({"error": f"Failed to list files: {str(e)}"}), 500


@app.route("/extract-calendar", methods=["POST"])
def extract_calendar():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No selected file"}), 400

    # Validate file type (PDF only)
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "Only PDF files are allowed."}), 400

    try:
        # Extract text from PDF
        file.seek(0)  # Reset file pointer
        reader = PdfReader(file)
        extracted_text = " ".join([page.extract_text() or "" for page in reader.pages])

        # Combine extracted text with the prompt
        prompt = (
            "Extract all dates and events from the given text. "
            "Output only a JSON array with each entry in the format: "
            '{"date": "mm-dd", "title": "...", "task": "..."} and ensure all dates are in the "mm-dd" format. '
            "Do not include any text outside the JSON array.\n\n"
            f"{extracted_text}"
        )

        # Call GPT API with the prompt
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract JSON from the response
        json_output = response.choices[0].message.content.strip()

        # Validate JSON output
        try:
            parsed_output = json.loads(json_output)
            if not isinstance(parsed_output, list):
                raise ValueError("Output is not a JSON array")
        except Exception as e:
            return jsonify({"error": "Failed to parse GPT output", "details": str(e)}), 500

        return jsonify(parsed_output), 200

    except Exception as e:
        print(f"Error extracting calendar events: {e}")
        return jsonify({"error": f"Failed to extract calendar events: {str(e)}"}), 500


if __name__ == "__main__":
    # Ensure Google Application Credentials are set
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "firebase-key.json"
    app.run(debug=True, port=5001)
