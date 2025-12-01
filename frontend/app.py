import os
import time
import boto3
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# -----------------------------
# CONFIG
# -----------------------------
load_dotenv()
# Use your real bucket names
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
INPUT_BUCKET = os.getenv("INPUT_BUCKET")
OUTPUT_BUCKET = os.getenv("OUTPUT_BUCKET")
SUMMARY_PREFIX = "summaries/"  # used by your Lambda

# Polling settings (after upload, how long we wait for summary)
MAX_WAIT_SECONDS = 180
POLL_INTERVAL_SECONDS = 5

app = Flask(__name__)
app.secret_key = "change-this-secret"  # needed for flash messages

# S3 client (uses default AWS credentials/profile)
s3 = boto3.client("s3",region_name=AWS_REGION)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", summary_text=None, filename=None, error=None)


@app.route("/upload", methods=["POST"])
def upload_file():
    if "document" not in request.files:
        flash("No file part in request")
        return redirect(url_for("index"))

    file = request.files["document"]
    if file.filename == "":
        flash("No file selected")
        return redirect(url_for("index"))

    filename = secure_filename(file.filename)

    try:
        # 1) Upload to input bucket (triggers your Lambda)
        s3.upload_fileobj(file, INPUT_BUCKET, filename)

        # 2) Poll output bucket for summary
        summary_key = f"{SUMMARY_PREFIX}{filename}.summary.txt"

        start = time.time()
        summary_text = None

        while time.time() - start < MAX_WAIT_SECONDS:
            try:
                obj = s3.get_object(Bucket=OUTPUT_BUCKET, Key=summary_key)
                body = obj["Body"].read()
                summary_text = body.decode("utf-8", errors="ignore")
                break  # summary found!
            except s3.exceptions.NoSuchKey:
                # Summary not ready yet
                time.sleep(POLL_INTERVAL_SECONDS)
            except Exception as e:
                # If it's an S3 permission or transient error, keep waiting
                # (after IAM fix this should basically disappear)
                print(f"Temporary S3 error while fetching summary: {e}")
                time.sleep(POLL_INTERVAL_SECONDS)

        if summary_text is None:
            # Not ready within our wait window
            msg = (
                "File uploaded successfully, but the summary is not ready yet. "
                "Please try again in a moment."
            )
            return render_template(
                "index.html",
                summary_text=None,
                filename=filename,
                error=msg,
            )

        # 3) Render page with summary
        return render_template(
            "index.html",
            summary_text=summary_text,
            filename=filename,
            error=None,
        )

    except Exception as e:
        return render_template(
            "index.html",
            summary_text=None,
            filename=None,
            error=f"Upload failed: {e}",
        )


if __name__ == "__main__":
    # Run the app locally
    # You can change host="0.0.0.0" if you want to test from another device on the LAN
    app.run(debug=True, host="127.0.0.1", port=5000)
