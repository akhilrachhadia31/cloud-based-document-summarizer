# 📄 Intelligent Document Auto-Summarization  
**Powered by Amazon Bedrock (DeepSeek V3), AWS Lambda, Textract & Serverless**

---

## 🚀 Live Deployment  
🔗 **Try the App Live:**  
http://document-summary-env.eba-qyhrcvm2.ap-south-1.elasticbeanstalk.com/

---

## ✨ Features
```
Component                          Description 

File Upload                        Upload **Text, PDF, Image (JPG, PNG)** files 
OCR                                Extracts text using Amazon Textract 
AI Summarization                   Generates clean summaries using **Amazon Bedrock (DeepSeek V3) 
Serverless                         Built using AWS Lambda & S3, auto-scales with demand 
Persistent Storage                 Stores original & summarized files in S3
Metadata Tracking                  Uses DynamoDB to store status, timestamps, and file metadata 
Simple UI                          Flask-based clean and responsive web interface 
Auto Processing                    Real-time summary fetching from AWS S3 
```

## System Architecture  
### Below is the actual architecture diagram used in this project:

![Architecture Diagram](./architecture.png)



##  🗃 Tech Stack
```
Layer	                                         Technology

Frontend	                                     Flask (Python)
Hosting	                                         AWS Elastic Beanstalk
File Storage	                                 Amazon S3
AI/LLM	                                         Amazon Bedrock (DeepSeek V3)
Compute	                                         AWS Lambda
Database	                                     Amazon DynamoDB
Infrastructure as Code	                         AWS CDK (Python)
```

## 📂 Project Structure
```
cloud-based-document-summarizer/
├── cdk-python/
│   ├── app.py
│   ├── cdk.json
│   ├── setup.py
│   ├── requirements.txt
│   ├── lambda/
│   │   └── index.py         # Main Lambda function (Textract + Bedrock)
│   └── cdk.out/
│
├── cdk-typescript/
│   ├── app.ts
│   ├── package.json
│   ├── tsconfig.json
│   └── README.md
│
├── frontend/
│   ├── app.py              # Flask backend
│   ├── application.py      # Elastic Beanstalk entry
│   ├── .env                # AWS and bucket config
│   ├── requirements.txt
│   ├── static/
│   │   └── style.css
│   ├── templates/
│   │   └── index.html
│   └── .elasticbeanstalk/
│       └── config.yml
│
├── scripts/
│   ├── deploy.sh
│   ├── destroy.sh
│   └── cdk_complete
│
├── cloudformation.yaml
├── .gitignore
└── README.md
```

## Getting Started (Local Setup)
```bash 
Clone Repository
git clone <your-repo-url>
cd document-auto-summarization
```
## Backend Deployment (AWS CDK)
```bash
cd cdk-python
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
npm install -g aws-cdk
cdk bootstrap
cdk deploy
✔ After deployment, note down the generated S3 bucket names
```

## Frontend Setup (Flask)
```bash
Create .env file inside frontend/env
AWS_REGION=ap-south-1
INPUT_BUCKET=<your-input-bucket-name>
OUTPUT_BUCKET=<your-output-bucket-name>
Run Locally
cd frontend
pip install -r requirements.txt
python app.py
Visit http://localhost:5000
```

### 🌐 Deploy on AWS Elastic Beanstalk
```bash
cd frontend
eb init -p python-3.11 document-summary-app
eb create document-summary-env
eb deploy
```

### 🔍 Monitoring & Troubleshooting
```bash
Tools	                          Usage

CloudWatch Logs	                  Lambda execution logs
CloudWatch Metrics	              Invocation stats, failures
S3 Output Bucket	              Final generated summaries
DynamoDB	                      File metadata and processing status
```
