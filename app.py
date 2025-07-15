from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Form
from fastapi.responses import FileResponse, Response
from ultralytics import YOLO
from PIL import Image
import sqlite3
import os
import uuid
import shutil
import boto3
from pydantic import BaseModel
from fastapi import Body
import time
import json
import asyncio

# Disable GPU usage
import torch
torch.cuda.is_available = lambda: False

app = FastAPI()

UPLOAD_DIR = "uploads/original"
PREDICTED_DIR = "uploads/predicted"
DB_PATH = "predictions.db"
S3_BUCKET = "haitham-polybot-dev"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PREDICTED_DIR, exist_ok=True)
s3 = boto3.client("s3")

# Download the AI model (tiny model ~6MB)
model = YOLO("yolov8n.pt")  

# Initialize SQLite
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        # Create the predictions main table to store the prediction session
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prediction_sessions (
                uid TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                original_image TEXT,
                predicted_image TEXT
            )
        """)
        
        # Create the objects table to store individual detected objects in a given image
        conn.execute("""
            CREATE TABLE IF NOT EXISTS detection_objects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_uid TEXT,
                label TEXT,
                score REAL,
                box TEXT,
                FOREIGN KEY (prediction_uid) REFERENCES prediction_sessions (uid)
            )
        """)
        
        # Create index for faster queries
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prediction_uid ON detection_objects (prediction_uid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_label ON detection_objects (label)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON detection_objects (score)")

init_db()




def save_prediction_session(uid, original_image, predicted_image,chat_id,service="DynamoDB"):
    """
    Save prediction session to database
    """
    if service == "sqlite3":
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                         INSERT INTO prediction_sessions (uid, original_image, predicted_image)
                         VALUES (?, ?, ?)
                         """, (uid, original_image, predicted_image))
            
    elif service == "DynamoDB":
        table = boto3.resource('dynamodb', region_name='us-east-1').Table('HaithamPredictionSessions')
        table.put_item(Item={
            "uid": uid,
            "original_image": original_image,
            "predicted_image": predicted_image,
            "chat_id" : chat_id
        })





def save_detection_object(prediction_uid, label, score, box,service="DynamoDB"):
    """
    Save detection object to database
    """
    if service == "sqlite3":
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                         INSERT INTO detection_objects (prediction_uid, label, score, box)
                         VALUES (?, ?, ?, ?)
                         """, (prediction_uid, label, score, str(box)))

    elif service == "DynamoDB":
        table = boto3.resource('dynamodb', region_name='us-east-1').Table('HaithamDetectionSessions')
        table.put_item(Item={
            "prediction_uid": prediction_uid,
            "label": label,
            "score": str(score),  
            "box": str(box)
            })





class ImageNameRequest(BaseModel):
    image_name: str

@app.post("/predict")
def predict():
    return
                    
def main ():
    """
    Predict objects in an image
    """
    import requests

    uid = str(uuid.uuid4())
    sqs = boto3.client('sqs', region_name='us-east-1')
    QUEUE_URL = 'https://sqs.us-east-1.amazonaws.com/228281126655/haitham-polybot-chat-messages'
    try : 
        while True:
            response = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=20)
    
            messages = response.get('Messages', [])
    
            for msg in messages:
                msg_body: dict = json.loads(msg['Body'])
                print(f"Handling message: {msg_body}")
        
                # Delete the message when done processing it
                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg['ReceiptHandle'])

                print(f"Message processed: {msg['MessageId']}")
                if msg and msg_body['image_name']:
                    ext = os.path.splitext(msg_body['image_name'])[1]
                    original_path = os.path.join(UPLOAD_DIR, uid + ext)
                    try:
                        s3.download_file(S3_BUCKET, msg_body['image_name'], original_path)
                    except Exception as e:
                        raise HTTPException(status_code=500, detail=f"S3 download failed: {str(e)}")
                    results = model(original_path, device="cpu")
                    predicted_path = os.path.join(PREDICTED_DIR, uid + os.path.splitext(original_path)[1])
                    annotated_frame = results[0].plot()  # NumPy image with boxes
                    annotated_image = Image.fromarray(annotated_frame)
                    annotated_image.save(predicted_path)
                    s3.upload_file(predicted_path, S3_BUCKET, predicted_path)
                    save_prediction_session(uid, original_path, predicted_path,msg_body['chat_id'])
                    detected_labels = []
                    for box in results[0].boxes:
                        label_idx = int(box.cls[0].item())
                        label = model.names[label_idx]
                        score = float(box.conf[0])
                        bbox = box.xyxy[0].tolist()
                        save_detection_object(uid, label, score, bbox)
                        detected_labels.append(label)
                    url = os.getenv("POLYBOT_URL", "polybot-dev")
                    url = f"http://{url}:8443/predictions/{uid}"
                    payload = {
                        "chat_id": msg_body['chat_id']
                        }

                    try:
                        response = requests.post(url, json=payload)
                        response.raise_for_status()
                        print(f"Notified Polybot for prediction_id: {uid}")
                    except requests.exceptions.RequestException as e:
                        print(f"Failed to notify Polybot: {e}")

            if not messages:
                time.sleep(1)
    except Exception as e:
        print(f"Error: {e}")
                    
    
    



@app.get("/prediction/{uid}")
def get_prediction_by_uid(uid: str):
    """
    Get prediction session by uid with all detected objects
    """
    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
    prediction_table = dynamodb.Table('HaithamPredictionSessions')
    detection_table = dynamodb.Table('HaithamDetectionSessions')
    try:
        prediction = prediction_table.get_item(Key={'uid': uid}).get('Item')
        if not prediction:
            raise HTTPException(status_code=404, detail="Prediction not found")

        # Get detection objects
        response = detection_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('prediction_uid').eq(uid)
        )
        detection_objects = response.get('Items', [])

        return {
            "uid": uid,
            "original_image": prediction.get("original_image"),
            "predicted_image": prediction.get("predicted_image"),
            "detection_objects": [
                {
                    "label": obj["label"],
                    "score": float(obj["score"]),
                    "box": obj["box"]
                } for obj in detection_objects
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {str(e)}")



















@app.get("/predictions/label/{label}")
def get_predictions_by_label(label: str):
    """
    Get prediction sessions containing objects with specified label
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT DISTINCT ps.uid, ps.timestamp
            FROM prediction_sessions ps
            JOIN detection_objects do ON ps.uid = do.prediction_uid
            WHERE do.label = ?
        """, (label,)).fetchall()
        
        return [{"uid": row["uid"], "timestamp": row["timestamp"]} for row in rows]

@app.get("/predictions/score/{min_score}")
def get_predictions_by_score(min_score: float):
    """
    Get prediction sessions containing objects with score >= min_score
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT DISTINCT ps.uid, ps.timestamp
            FROM prediction_sessions ps
            JOIN detection_objects do ON ps.uid = do.prediction_uid
            WHERE do.score >= ?
        """, (min_score,)).fetchall()
        
        return [{"uid": row["uid"], "timestamp": row["timestamp"]} for row in rows]

@app.get("/image/{type}/{filename}")
def get_image(type: str, filename: str):
    """
    Get image by type and filename
    """
    if type not in ["original", "predicted"]:
        raise HTTPException(status_code=400, detail="Invalid image type")
    path = os.path.join("uploads", type, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)

@app.get("/prediction/{uid}/image")
def get_prediction_image(uid: str, request: Request):
    """
    Get prediction image by uid
    """
    accept = request.headers.get("accept", "")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT predicted_image FROM prediction_sessions WHERE uid = ?", (uid,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Prediction not found")
        image_path = row[0]

    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="Predicted image file not found")

    if "image/png" in accept:
        return FileResponse(image_path, media_type="image/png")
    elif "image/jpeg" in accept or "image/jpg" in accept:
        return FileResponse(image_path, media_type="image/jpeg")
    else:
        # If the client doesn't accept image, respond with 406 Not Acceptable
        raise HTTPException(status_code=406, detail="Client does not accept an image format")

@app.get("/health")
def health():
    """
    Health check endpoint
    """
    return {"status": "ok"}


import threading
@app.on_event("startup")
def start_sqs_polling():
    thread = threading.Thread(target=main, daemon=True)
    thread.start()
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
