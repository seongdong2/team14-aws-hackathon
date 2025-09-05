# SNS Topic for Alerts
resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "rescuebot" {
  name              = "/aws/ec2/rescuebot"
  retention_in_days = 7
}

# CloudWatch Alarm for MySQL Port
resource "aws_cloudwatch_metric_alarm" "mysql_port_down" {
  alarm_name          = "${var.project_name}-mysql-port-down"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "Port3306Status"
  namespace           = "Custom/MySQL"
  period              = "60"
  statistic           = "Average"
  threshold           = "1"
  alarm_description   = "This metric monitors MySQL port 3306 status"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${var.project_name}-mysql-port-alarm"
  }
}

# EventBridge Rule
resource "aws_cloudwatch_event_rule" "mysql_alert" {
  name        = "${var.project_name}-mysql-alert-rule"
  description = "Capture MySQL port down events"

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      alarmName = [aws_cloudwatch_metric_alarm.mysql_port_down.alarm_name]
      state = {
        value = ["ALARM"]
      }
    }
  })
}

# EventBridge Target - Lambda
resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.mysql_alert.name
  target_id = "MySQLAlertLambdaTarget"
  arn       = aws_lambda_function.alert_handler.arn
}

# Lambda permission for EventBridge
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.alert_handler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.mysql_alert.arn
}

# Lambda Function for Alert Handling
resource "aws_lambda_function" "alert_handler" {
  filename         = "alert_handler.zip"
  function_name    = "${var.project_name}-alert-handler"
  role            = aws_iam_role.lambda_role.arn
  handler         = "lambda_function.lambda_handler"
  runtime         = "python3.9"
  timeout         = 60

  environment {
    variables = {
      SNS_TOPIC_ARN = aws_sns_topic.alerts.arn
      FLASK_API_URL = "http://${aws_lb.main.dns_name}"
    }
  }

  depends_on = [data.archive_file.lambda_zip]
}

# Lambda function code
resource "local_file" "lambda_code" {
  content = <<EOF
import json
import boto3
import urllib3

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    sns = boto3.client('sns')
    http = urllib3.PoolManager()
    
    # Send SNS notification
    message = {
        "alert": "MySQL Port 3306 Down",
        "server": event.get('detail', {}).get('configuration', {}).get('dimensions', {}).get('InstanceId', 'unknown'),
        "timestamp": event.get('time', 'unknown')
    }
    
    try:
        # Send to SNS (Slack)
        sns.publish(
            TopicArn=os.environ['SNS_TOPIC_ARN'],
            Message=json.dumps(message),
            Subject="MySQL Alert - Port Down"
        )
        
        # Call Flask API
        flask_url = f"{os.environ['FLASK_API_URL']}/webhook/cloudwatch"
        response = http.request('POST', flask_url, 
                              body=json.dumps(message),
                              headers={'Content-Type': 'application/json'})
        
        return {
            'statusCode': 200,
            'body': json.dumps('Alert processed successfully')
        }
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error: {str(e)}')
        }
EOF
  filename = "${path.module}/lambda_function.py"
}

# Create Lambda deployment package
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = local_file.lambda_code.filename
  output_path = "${path.module}/alert_handler.zip"
  depends_on  = [local_file.lambda_code]
}

# Bedrock Lambda Function
resource "aws_lambda_function" "bedrock_handler" {
  filename         = "bedrock_handler.zip"
  function_name    = "${var.project_name}-bedrock-handler"
  role            = aws_iam_role.lambda_role.arn
  handler         = "bedrock_function.lambda_handler"
  runtime         = "python3.9"
  timeout         = 300

  depends_on = [data.archive_file.bedrock_lambda_zip]
}

resource "local_file" "bedrock_lambda_code" {
  content = <<EOF
import json
import boto3

def lambda_handler(event, context):
    bedrock = boto3.client('bedrock-runtime')
    
    try:
        prompt = event.get('prompt', 'MySQL service is down. Provide troubleshooting steps.')
        
        response = bedrock.invoke_model(
            modelId='anthropic.claude-3-sonnet-20240229-v1:0',
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1000,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            })
        )
        
        result = json.loads(response['body'].read())
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'solution': result['content'][0]['text']
            })
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error: {str(e)}')
        }
EOF
  filename = "${path.module}/bedrock_function.py"
}

data "archive_file" "bedrock_lambda_zip" {
  type        = "zip"
  source_file = local_file.bedrock_lambda_code.filename
  output_path = "${path.module}/bedrock_handler.zip"
  depends_on  = [local_file.bedrock_lambda_code]
}