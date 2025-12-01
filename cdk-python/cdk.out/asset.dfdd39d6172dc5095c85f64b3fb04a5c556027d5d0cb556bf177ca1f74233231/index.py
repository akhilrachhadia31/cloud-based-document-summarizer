# index.py
import json
import boto3
import os
from urllib.parse import unquote_plus
import logging
from datetime import datetime, timezone
import uuid
from typing import Dict, Any, Optional
import re

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
s3 = boto3.client("s3")
textract = boto3.client("textract")
bedrock = boto3.client("bedrock-runtime")
dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")

# Env vars
OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
METADATA_TABLE = os.environ["METADATA_TABLE"]
NOTIFICATION_TOPIC = os.environ["NOTIFICATION_TOPIC"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

table = dynamodb.Table(METADATA_TABLE)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda handler."""
    document_id: Optional[str] = None
    processing_timestamp = datetime.now(timezone.utc).isoformat()

    try:
        bucket = event["Records"][0]["s3"]["bucket"]["name"]
        key = unquote_plus(event["Records"][0]["s3"]["object"]["key"])

        logger.info("Processing document: %s from bucket: %s", key, bucket)

        document_id = str(uuid.uuid4())

        store_metadata(document_id, key, "PROCESSING", processing_timestamp)

        text_content = extract_text(bucket, key)
        logger.info("Extracted %d characters of text", len(text_content))

        if not text_content.strip():
            raise ValueError("No text could be extracted from the document.")

        summary = generate_summary(text_content)
        logger.info("Generated summary of %d characters", len(summary))

        summary_key = store_summary(key, summary, text_content)

        processing_duration = (
            datetime.now(timezone.utc) - datetime.fromisoformat(processing_timestamp)
        ).total_seconds()

        store_metadata(
            document_id,
            key,
            "COMPLETED",
            processing_timestamp,
            {
                "summary_key": summary_key,
                "text_length": len(text_content),
                "summary_length": len(summary),
                "processing_duration": str(processing_duration),
            },
        )

        send_notification(document_id, key, summary_key, "SUCCESS")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Document processed successfully",
                    "document_id": document_id,
                    "document": key,
                    "summary_key": summary_key,
                    "summary_length": len(summary),
                }
            ),
        }

    except Exception as e:
        logger.exception("Error processing document: %s", e)

        try:
            store_metadata(
                document_id or "UNKNOWN",
                key if "key" in locals() else "UNKNOWN",
                "FAILED",
                processing_timestamp,
                {"error_message": str(e)},
            )
            send_notification(
                document_id or "UNKNOWN",
                key if "key" in locals() else "UNKNOWN",
                None,
                "FAILED",
                str(e),
            )
        except Exception as inner:
            logger.error(
                "Failed to record failure metadata/notification: %s", inner
            )

        raise

def _clean_extracted_text(raw: str) -> str:
    """
    Try to clean up text that may contain raw PDF structure or binary-looking noise.
    Removes obvious PDF markers and keeps only reasonably printable characters.
    """
    lines = raw.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue

        lower = stripped.lower()

        # Skip common PDF structural lines
        if lower.startswith("%pdf"):
            continue
        if lower.startswith("%%eof"):
            continue
        if re.match(r"^\d+\s+\d+\s+obj$", stripped):  # '12 0 obj'
            continue
        if lower in ("endobj", "stream", "endstream"):
            continue

        cleaned_lines.append(stripped)

    cleaned = "\n".join(cleaned_lines)

    # Keep only printable characters (plus newline / tab)
    cleaned = "".join(
        ch
        for ch in cleaned
        if ch in ("\n", "\t") or 32 <= ord(ch) <= 126 or ord(ch) >= 160
    )

    return cleaned



def extract_text(bucket: str, key: str) -> str:
    """
    Extract text from document.

    - For .txt/.md/.csv/.log: read directly from S3
    - Otherwise: use Textract (PDF, images)
    """
    try:
        extension = os.path.splitext(key.lower())[1]

        if extension in [".txt", ".md", ".csv", ".log"]:
            obj = s3.get_object(Bucket=bucket, Key=key)
            data = obj["Body"].read()
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return data.decode("latin-1", errors="ignore")

        # Use Textract for PDFs/images
        response = textract.detect_document_text(
            Document={"S3Object": {"Bucket": bucket, "Name": key}}
        )

        text_blocks = [
            block["Text"]
            for block in response.get("Blocks", [])
            if block.get("BlockType") == "LINE"
        ]
        return "\n".join(text_blocks)
    except textract.exceptions.UnsupportedDocumentException as e:
        logger.error("Unsupported document format for Textract: %s", e)
        # Fallback: try to read as plain text, then aggressively clean it
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
        raw_text = data.decode("utf-8", errors="ignore")
        cleaned = _clean_extracted_text(raw_text)
        return cleaned


    except Exception as e:
        logger.error("Text extraction failed: %s", e)
        raise

def _generic_failure_summary() -> str:
    """
    Fallback text if the model thinks the document is corrupted / binary.
    Kept short and plain, no AI-ish phrasing.
    """
    return (
        "1. The document text could not be read in a clear way.\n"
        "2. No reliable topics, facts, or dates can be identified from the available data.\n"
        "3. Consider uploading a text-based or searchable PDF version of the file.\n"
        "4. If the file is scanned, try running OCR locally and uploading the extracted text."
    )


def generate_summary(text_content: str) -> str:
    """Generate summary using DeepSeek V3 on Amazon Bedrock."""
    try:
        max_length = 10000
        if len(text_content) > max_length:
            text_content = text_content[:max_length] + "..."
            logger.warning("Text truncated to %d characters", max_length)

        # Extra safety: one more clean pass
        text_content = _clean_extracted_text(text_content)

        prompt_system = (
            "You write natural, human-sounding summaries of documents.\n"
            "Rules:\n"
            "- Do not apologize.\n"
            "- Do not mention binary data, encoding, PDF structure, or raw bytes.\n"
            "- Do not say the document is corrupted or unreadable.\n"
            "- Do not use markdown, bullet symbols, or asterisks (*, -, •, etc.).\n"
            "- Write plain text only, with numbered points and short paragraphs.\n"
            "If you truly cannot see any meaningful text at all, keep the answer very short."
        )

        prompt_user = (
            "Summarize the following document clearly and concisely.\n"
            "Include:\n"
            "1. Main topics and key points\n"
            "2. Important facts, figures, and conclusions\n"
            "3. Actionable insights or recommendations\n"
            "4. Any critical deadlines or dates mentioned\n\n"
            "Document content:\n"
            f"{text_content}\n"
        )

        body = {
            "model": BEDROCK_MODEL_ID,
            "messages": [
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": prompt_user},
            ],
            "max_tokens": 800,
            "temperature": 0.1,
            "top_p": 0.9,
        }

        response = bedrock.invoke_model(
            modelId="deepseek.v3-v1:0",
            body=json.dumps(body),
        )

        response_body = json.loads(response["body"].read())
        summary_text = response_body["choices"][0]["message"]["content"]

        # Remove typical formatting characters
        for ch in ["*", "•", "●", "-", "▪"]:
            summary_text = summary_text.replace(ch, "")

        summary_text = summary_text.strip()

        # If the model still insists on the binary / corrupted story, replace it
        lower = summary_text.lower()
        bad_phrases = [
            "appears to be corrupted",
            "contains unreadable data",
            "not in a recognizable text format",
            "not presented in a coherent, textual format",
            "raw, binary data",
        ]
        if any(p in lower for p in bad_phrases):
            logger.warning("Model produced a 'corrupted/binary' explanation. Using fallback.")
            summary_text = _generic_failure_summary()

        return summary_text

    except Exception as e:
        logger.error("Summary generation failed: %s", e)
        raise



def store_summary(original_key: str, summary: str, full_text: str) -> str:
    """Store summary and metadata in S3."""
    try:
        summary_key = f"summaries/{original_key}.summary.txt"

        s3.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=summary_key,
            Body=summary,
            ContentType="text/plain",
            Metadata={
                "original-document": original_key,
                "summary-generated": "true",
                "text-length": str(len(full_text)),
                "summary-length": str(len(summary)),
                "processing-timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        logger.info("Summary stored: %s", summary_key)
        return summary_key

    except Exception as e:
        logger.error("Failed to store summary: %s", e)
        raise


def store_metadata(
    document_id: str,
    document_key: str,
    status: str,
    timestamp: str,
    additional_data: Optional[Dict] = None,
) -> None:
    """Store document processing metadata in DynamoDB."""
    try:
        item: Dict[str, Any] = {
            "document_id": document_id,
            "processing_timestamp": timestamp,
            "document_key": document_key,
            "processing_status": status,
            "ttl": int(
                datetime.now(timezone.utc).timestamp() + 30 * 24 * 60 * 60
            ),  # 30 days
        }

        if additional_data:
            item.update(additional_data)

        table.put_item(Item=item)
        logger.info("Metadata stored for document %s", document_id)

    except Exception as e:
        logger.error("Failed to store metadata: %s", e)


def send_notification(
    document_id: str,
    document_key: str,
    summary_key: Optional[str],
    status: str,
    error_message: Optional[str] = None,
) -> None:
    """Send SNS notification."""
    try:
        message: Dict[str, Any] = {
            "document_id": document_id,
            "document_key": document_key,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if summary_key:
            message["summary_key"] = summary_key

        if error_message:
            message["error_message"] = error_message

        sns.publish(
            TopicArn=NOTIFICATION_TOPIC,
            Subject=f"Document Processing {status}: {document_key}",
            Message=json.dumps(message, indent=2),
        )

        logger.info("Notification sent for document %s", document_id)

    except Exception as e:
        logger.error("Failed to send notification: %s", e)
