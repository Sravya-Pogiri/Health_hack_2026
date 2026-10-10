"""Relay on AWS. Every service is here because a rule in the brief or a concern from Sevaro needs it.

  S3 (versioned, KMS)        event data lake: kit/, raw/ (every file ever added), processed/, manifests/
  Lambda relay-ingest        a CSV lands in raw/ -> validate 8 columns -> write manifest -> engine reload
  App Runner (ECR image)     FastAPI engine plus the React frontend, HTTPS, one instance (state in memory)
  DynamoDB relay-actions     offers, approvals, replies; conditional writes make "first acceptance wins" safe
  DynamoDB relay-audit       append-only audit log
  EventBridge Scheduler      every minute -> Lambda relay-tick -> /api/timers/tick (expiry, escalation, no-shows)
  SNS relay-offers           guarded offer text to a demo phone or email
  SNS relay-coordinator-alerts  internal alerts, including the CloudWatch "feed went quiet" alarm
  CloudWatch                 logs, custom metrics (namespace Relay), DataAgeMinutes alarm
  KMS, Secrets Manager, IAM  encryption at rest, the shared secret for internal calls, least-privilege roles
  Bedrock                    permission for the clinical lead summary (template fallback if unavailable)
"""
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apprunner as apprunner
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

ROOT = Path(__file__).resolve().parent.parent


class RelayStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        key = kms.Key(self, "RelayKey", enable_key_rotation=True, alias="alias/relay",
                      removal_policy=RemovalPolicy.DESTROY)

        # --- Event data lake ---------------------------------------------------------------
        bucket = s3.Bucket(
            self, "Events", versioned=True, encryption=s3.BucketEncryption.KMS, encryption_key=key,
            bucket_key_enabled=True, block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(id="raw-to-glacier", prefix="raw/", transitions=[
                    s3.Transition(storage_class=s3.StorageClass.GLACIER, transition_after=Duration.days(30))]),
                s3.LifecycleRule(id="old-versions", noncurrent_version_expiration=Duration.days(90)),
            ],
            cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.PUT], allowed_origins=["*"], allowed_headers=["*"])],
        )
        s3deploy.BucketDeployment(self, "KitFiles", sources=[s3deploy.Source.asset(str(ROOT / "data" / "kit"))],
                                  destination_bucket=bucket, destination_key_prefix="kit/", prune=False)

        # --- Actions and audit ---------------------------------------------------------------
        actions = ddb.Table(
            self, "Actions", table_name="relay-actions",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST, encryption=ddb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=key, time_to_live_attribute="ttl",
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            removal_policy=RemovalPolicy.DESTROY)
        audit = ddb.Table(
            self, "Audit", table_name="relay-audit",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST, encryption=ddb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=key, removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True))

        secret = secretsmanager.Secret(
            self, "SharedSecret", description="Shared secret for Relay internal calls (reload, timer tick)",
            generate_secret_string=secretsmanager.SecretStringGenerator(exclude_punctuation=True, password_length=40))

        # --- Notifications ---------------------------------------------------------------------
        offers_topic = sns.Topic(self, "Offers", topic_name="relay-offers", master_key=key)
        alerts_topic = sns.Topic(self, "Alerts", topic_name="relay-coordinator-alerts", master_key=key)
        if self.node.try_get_context("offer_email"):
            offers_topic.add_subscription(subs.EmailSubscription(self.node.try_get_context("offer_email")))
        if self.node.try_get_context("alert_email"):
            alerts_topic.add_subscription(subs.EmailSubscription(self.node.try_get_context("alert_email")))
        key.add_to_resource_policy(iam.PolicyStatement(
            actions=["kms:Decrypt", "kms:GenerateDataKey*"],
            principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com")], resources=["*"]))

        # --- Engine on App Runner ------------------------------------------------------------
        image = ecr_assets.DockerImageAsset(self, "Image", directory=str(ROOT),
                                            platform=ecr_assets.Platform.LINUX_AMD64)
        access_role = iam.Role(self, "AppRunnerAccessRole",
                               assumed_by=iam.ServicePrincipal("build.apprunner.amazonaws.com"))
        image.repository.grant_pull(access_role)

        instance_role = iam.Role(self, "AppRunnerInstanceRole",
                                 assumed_by=iam.ServicePrincipal("tasks.apprunner.amazonaws.com"))
        bucket.grant_read_write(instance_role)
        bucket.grant_delete(instance_role)
        actions.grant_read_write_data(instance_role)
        audit.grant_read_write_data(instance_role)
        offers_topic.grant_publish(instance_role)
        alerts_topic.grant_publish(instance_role)
        secret.grant_read(instance_role)
        key.grant_encrypt_decrypt(instance_role)
        instance_role.add_to_policy(iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"], resources=["*"],
            conditions={"StringEquals": {"cloudwatch:namespace": "Relay"}}))
        instance_role.add_to_policy(iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=["*"]))

        scaling = apprunner.CfnAutoScalingConfiguration(
            self, "SingleInstance", auto_scaling_configuration_name="relay-single", min_size=1, max_size=1,
            max_concurrency=100)
        env = {
            "RELAY_ENV": "aws", "RELAY_BUCKET": bucket.bucket_name, "RELAY_ACTIONS_TABLE": actions.table_name,
            "RELAY_AUDIT_TABLE": audit.table_name, "RELAY_OFFERS_TOPIC_ARN": offers_topic.topic_arn,
            "RELAY_ALERTS_TOPIC_ARN": alerts_topic.topic_arn, "RELAY_TICKER": "0", "PORT": "8080",
        }
        service = apprunner.CfnService(
            self, "Service", service_name="relay",
            source_configuration=apprunner.CfnService.SourceConfigurationProperty(
                auto_deployments_enabled=False,
                authentication_configuration=apprunner.CfnService.AuthenticationConfigurationProperty(
                    access_role_arn=access_role.role_arn),
                image_repository=apprunner.CfnService.ImageRepositoryProperty(
                    image_identifier=image.image_uri, image_repository_type="ECR",
                    image_configuration=apprunner.CfnService.ImageConfigurationProperty(
                        port="8080",
                        runtime_environment_variables=[apprunner.CfnService.KeyValuePairProperty(name=k, value=v)
                                                       for k, v in env.items()],
                        runtime_environment_secrets=[apprunner.CfnService.KeyValuePairProperty(
                            name="RELAY_SHARED_SECRET", value=secret.secret_arn)]))),
            instance_configuration=apprunner.CfnService.InstanceConfigurationProperty(
                cpu="1 vCPU", memory="2 GB", instance_role_arn=instance_role.role_arn),
            health_check_configuration=apprunner.CfnService.HealthCheckConfigurationProperty(
                protocol="HTTP", path="/api/health", interval=10, timeout=5),
            auto_scaling_configuration_arn=scaling.attr_auto_scaling_configuration_arn,
            encryption_configuration=apprunner.CfnService.EncryptionConfigurationProperty(kms_key=key.key_arn),
        )
        service_url = "https://" + service.attr_service_url

        # --- Ingest and timer Lambdas ----------------------------------------------------------
        fn_env = {"SERVICE_URL": service_url, "SECRET_ARN": secret.secret_arn}
        code = lambda_.Code.from_asset(str(Path(__file__).parent / "lambda"))
        ingest = lambda_.Function(
            self, "Ingest", function_name="relay-ingest", runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.ingest", code=code, timeout=Duration.seconds(60), environment=fn_env,
            log_group=logs.LogGroup(self, "IngestLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY))
        bucket.grant_read_write(ingest)
        secret.grant_read(ingest)
        key.grant_encrypt_decrypt(ingest)
        bucket.add_event_notification(s3.EventType.OBJECT_CREATED, s3n.LambdaDestination(ingest),
                                      s3.NotificationKeyFilter(prefix="raw/", suffix=".csv"))

        tick = lambda_.Function(
            self, "Tick", function_name="relay-tick", runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.tick", code=code, timeout=Duration.seconds(30), environment=fn_env,
            log_group=logs.LogGroup(self, "TickLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY))
        secret.grant_read(tick)
        key.grant_decrypt(tick)
        scheduler_role = iam.Role(self, "SchedulerRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        tick.grant_invoke(scheduler_role)
        scheduler.CfnSchedule(
            self, "TimerTick", name="relay-timer-tick", schedule_expression="rate(1 minute)",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(arn=tick.function_arn, role_arn=scheduler_role.role_arn))

        # --- Monitoring ----------------------------------------------------------------------
        data_age = cw.Metric(namespace="Relay", metric_name="DataAgeMinutes", period=Duration.minutes(1),
                             statistic="Maximum")
        quiet = cw.Alarm(
            self, "FeedQuiet", alarm_name="relay-feed-quiet", metric=data_age, threshold=10, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.BREACHING,
            alarm_description="No new Synapse events for 10 minutes (or Relay stopped reporting).")
        quiet.add_alarm_action(cw_actions.SnsAction(alerts_topic))
        cw.Dashboard(self, "Dashboard", dashboard_name="relay", widgets=[[
            cw.GraphWidget(title="Data age (minutes)", left=[data_age]),
            cw.GraphWidget(title="Open critical issues", left=[cw.Metric(namespace="Relay",
                                                                         metric_name="OpenCriticalIssues")]),
            cw.GraphWidget(title="Offers and alerts", left=[
                cw.Metric(namespace="Relay", metric_name="OffersSent"),
                cw.Metric(namespace="Relay", metric_name="OffersAccepted"),
                cw.Metric(namespace="Relay", metric_name="InternalAlerts")]),
        ]])

        if self.node.try_get_context("create_trail"):
            cloudtrail.Trail(self, "Trail", is_multi_region_trail=False, include_global_service_events=True)

        CfnOutput(self, "ServiceUrl", value=service_url)
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "OffersTopicArn", value=offers_topic.topic_arn)
        CfnOutput(self, "AlertsTopicArn", value=alerts_topic.topic_arn)
        CfnOutput(self, "SharedSecretArn", value=secret.secret_arn)
