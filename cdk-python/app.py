#!/usr/bin/env python3
"""
AWS CDK Python Application for Intelligent Document Summarization

Deploys:
- S3 input bucket (documents)
- S3 output bucket (summaries)
- Lambda for processing
- DynamoDB metadata table
- SNS notifications
- CloudWatch dashboard
"""

import os
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    aws_sns as sns,
    aws_cloudwatch as cloudwatch,
    aws_s3_notifications as s3n,
    aws_logs as logs,
)
from constructs import Construct


class DocumentSummarizationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Use Amazon Deepseek so you don't need Marketplace payment for Anthropic
        self.bedrock_model_id = "deepseek.v3-v1:0"
        self.lambda_timeout_minutes = 5
        self.lambda_memory_mb = 512

        self._create_storage_resources()
        self._create_database_resources()
        self._create_notification_resources()
        self._create_processing_resources()
        self._create_monitoring_resources()
        self._configure_event_triggers()
        self._create_outputs()

    def _create_storage_resources(self) -> None:
        # Input bucket
        self.input_bucket = s3.Bucket(
            self,
            "DocumentInputBucket",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,  # DEMO ONLY
            auto_delete_objects=True,  # DEMO ONLY
        )

        # Output bucket
        self.output_bucket = s3.Bucket(
            self,
            "SummaryOutputBucket",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,  # DEMO ONLY
            auto_delete_objects=True,  # DEMO ONLY
        )

    def _create_database_resources(self) -> None:
        self.metadata_table = dynamodb.Table(
            self,
            "DocumentMetadataTable",
            partition_key=dynamodb.Attribute(
                name="document_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="processing_timestamp", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,  # DEMO ONLY
            point_in_time_recovery=True,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
        )

        self.metadata_table.add_global_secondary_index(
            index_name="ProcessingStatusIndex",
            partition_key=dynamodb.Attribute(
                name="processing_status", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="processing_timestamp", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

    def _create_notification_resources(self) -> None:
        self.notification_topic = sns.Topic(
            self,
            "DocumentProcessingNotifications",
            display_name="Document Summarization Notifications",
        )

    def _create_processing_resources(self) -> None:
        # Lambda role
        self.lambda_role = iam.Role(
            self,
            "DocumentProcessorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
            inline_policies={
                "DocumentProcessingPolicy": iam.PolicyDocument(
                    statements=[
                        # S3 read input
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["s3:GetObject", "s3:GetObjectVersion"],
                            resources=[f"{self.input_bucket.bucket_arn}/*"],
                        ),
                        # S3 write output
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["s3:PutObject", "s3:PutObjectAcl"],
                            resources=[f"{self.output_bucket.bucket_arn}/*"],
                        ),
                        # Textract
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "textract:DetectDocumentText",
                                "textract:AnalyzeDocument",
                                "textract:StartDocumentTextDetection",
                                "textract:GetDocumentTextDetection",
                            ],
                            resources=["*"],
                        ),
                        # Bedrock
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["bedrock:InvokeModel"],
                            resources=[
                                f"arn:aws:bedrock:{self.region}::foundation-model/{self.bedrock_model_id}"
                            ],
                        ),
                        # DynamoDB
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "dynamodb:PutItem",
                                "dynamodb:UpdateItem",
                                "dynamodb:GetItem",
                                "dynamodb:Query",
                            ],
                            resources=[
                                self.metadata_table.table_arn,
                                f"{self.metadata_table.table_arn}/index/*",
                            ],
                        ),
                        # SNS
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["sns:Publish"],
                            resources=[self.notification_topic.topic_arn],
                        ),
                    ]
                )
            },
        )

        # IMPORTANT: use from_asset, not from_inline
        self.processor_function = _lambda.Function(
            self,
            "DocumentProcessorFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="index.lambda_handler",
            code=_lambda.Code.from_asset("lambda"),  # directory with index.py
            role=self.lambda_role,
            timeout=Duration.minutes(self.lambda_timeout_minutes),
            memory_size=self.lambda_memory_mb,
            environment={
                "OUTPUT_BUCKET": self.output_bucket.bucket_name,
                "METADATA_TABLE": self.metadata_table.table_name,
                "NOTIFICATION_TOPIC": self.notification_topic.topic_arn,
                "BEDROCK_MODEL_ID": self.bedrock_model_id,
            },
            retry_attempts=2,
            dead_letter_queue_enabled=True,
            log_retention=logs.RetentionDays.ONE_WEEK,
        )

    def _create_monitoring_resources(self) -> None:
        self.dashboard = cloudwatch.Dashboard(
            self,
            "DocumentSummarizationDashboard",
            dashboard_name=f"document-summarization-{self.stack_name}",
            widgets=[
                [
                    cloudwatch.GraphWidget(
                        title="Lambda Function Metrics",
                        left=[
                            self.processor_function.metric_invocations(),
                            self.processor_function.metric_errors(),
                            self.processor_function.metric_duration(),
                        ],
                        width=12,
                        height=6,
                    )
                ],
                [
                    cloudwatch.GraphWidget(
                        title="S3 Storage Metrics",
                        left=[
                            cloudwatch.Metric(
                                namespace="AWS/S3",
                                metric_name="NumberOfObjects",
                                dimensions_map={
                                    "BucketName": self.input_bucket.bucket_name,
                                    "StorageType": "AllStorageTypes",
                                },
                            ),
                            cloudwatch.Metric(
                                namespace="AWS/S3",
                                metric_name="BucketSizeBytes",
                                dimensions_map={
                                    "BucketName": self.output_bucket.bucket_name,
                                    "StorageType": "StandardStorage",
                                },
                            ),
                        ],
                        width=12,
                        height=6,
                    )
                ],
                [
                    cloudwatch.GraphWidget(
                        title="DynamoDB Metrics",
                        left=[
                            self.metadata_table.metric_consumed_read_capacity_units(),
                            self.metadata_table.metric_consumed_write_capacity_units(),
                        ],
                        width=12,
                        height=6,
                    )
                ],
            ],
        )

    def _configure_event_triggers(self) -> None:
        self.input_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(self.processor_function),
        )

    def _create_outputs(self) -> None:
        CfnOutput(
            self,
            "InputBucketName",
            value=self.input_bucket.bucket_name,
            description="S3 bucket for document uploads",
        )

        CfnOutput(
            self,
            "OutputBucketName",
            value=self.output_bucket.bucket_name,
            description="S3 bucket for generated summaries",
        )

        CfnOutput(
            self,
            "ProcessorFunctionName",
            value=self.processor_function.function_name,
            description="Lambda function for document processing",
        )

        CfnOutput(
            self,
            "MetadataTableName",
            value=self.metadata_table.table_name,
            description="DynamoDB table for document metadata",
        )

        CfnOutput(
            self,
            "NotificationTopicArn",
            value=self.notification_topic.topic_arn,
            description="SNS topic for processing notifications",
        )

        CfnOutput(
            self,
            "DashboardURL",
            value=(
                f"https://{self.region}.console.aws.amazon.com/cloudwatch/home"
                f"?region={self.region}#dashboards:name={self.dashboard.dashboard_name}"
            ),
            description="CloudWatch dashboard URL for monitoring",
        )


# CDK app bootstrap
app = cdk.App()

stack_name = app.node.try_get_context("stackName") or "DocumentSummarizationStack"

DocumentSummarizationStack(
    app,
    stack_name,
    description="Intelligent Document Summarization with Amazon Bedrock and Lambda",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "ap-south-1"),
    ),
    tags={
        "Application": "DocumentSummarization",
        "Environment": app.node.try_get_context("environment") or "development",
        "Owner": "CDK",
        "CostCenter": "AI-ML",
    },
)

app.synth()
