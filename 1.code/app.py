from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_restx import Api, Resource, fields
from datetime import datetime, timedelta
from dotenv import load_dotenv
from botocore.exceptions import NoCredentialsError, ClientError
import pymysql
import boto3
import json
import os
import requests
try:
    from scheduler import batch_scheduler
except ImportError:
    print("Warning: scheduler module not found. Batch functionality will be limited.")
    batch_scheduler = None

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Database configuration from .env
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', '3306'))
DB_USER = os.getenv('DB_USER', 'admin')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'tt')
DB_NAME = os.getenv('DB_NAME', 'rescuebot')

# SQLAlchemy configuration
app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# PyMySQL configuration for direct connection
DB_CONFIG = {
    'host': DB_HOST,
    'port': DB_PORT,
    'user': DB_USER,
    'password': DB_PASSWORD,
    'database': DB_NAME,
    'charset': 'utf8mb4'
}

# SaltStack configuration
SALT_API_URL = os.getenv('SALT_API_URL', 'http://localhost:8000')
SALT_USERNAME = os.getenv('SALT_USERNAME', 'saltapi')
SALT_PASSWORD = os.getenv('SALT_PASSWORD', 'saltapi')

# Slack configuration
SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL', 'https://hooks.slack.com/services/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')

def get_db_connection():
    """데이터베이스 연결 생성"""
    return pymysql.connect(**DB_CONFIG)

def send_slack_notification(message):
    """Slack webhook으로 메시지 전송"""
    if not SLACK_WEBHOOK_URL:
        print("Warning: SLACK_WEBHOOK_URL not configured")
        return False
    
    try:
        payload = {
            "text": message,
            "icon_emoji": ":robot_face:"
        }
        
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Slack notification failed: {e}")
        return False

def get_minion_id_by_fqdn(fqdn):
    """FQDN으로 minion_id 찾기"""
    try:
        grains_result = execute_salt_command('*', 'grains.items', timeout=10)
        if 'error' in grains_result:
            return None
        
        grains_data = grains_result.get('return', [{}])[0] if grains_result.get('return') else {}
        
        for minion_id, grains in grains_data.items():
            minion_fqdn = grains.get('fqdn', '')
            if minion_fqdn == fqdn:
                return minion_id
        return None
    except Exception as e:
        print(f"Error finding minion by FQDN: {e}")
        return None

def execute_mysql_restart(metric_host):
    """MySQL 재시작 명령어 실행"""
    minion_id = get_minion_id_by_fqdn(metric_host)
    print(f"Found minion_id: {minion_id} for host: {metric_host}")
    if not minion_id:
        return {'error': f'Minion not found for host: {metric_host}'}
    
    result = execute_salt_command(minion_id, 'service.restart', ['mysql'])
    return result

def get_salt_command_by_description(alarm_description, metric_host=None):
    """alarm_description 기반으로 Salt 명령어 조회 및 실행"""
    print(alarm_description, metric_host)
    print(f"Analyzing alarm_description: {alarm_description} for host: {metric_host}")
    if alarm_description and 'mysql connection error' in alarm_description.lower():
        print("Alarm description indicates MySQL connection error.")
        if metric_host:
            # 실제 MySQL 재시작 실행
            result = execute_mysql_restart(metric_host)
            return f'salt "{metric_host}" service.restart mysql', result
        return 'salt "*" service.restart mysql', None
    return None, None

def get_salt_command(trigger_namespace, trigger_metric_name):
    """Salt DB에서 명령어 조회 (현재는 시뮬레이션)"""
    salt_commands = {
        'AWS/EC2': {
            'CPUUtilization': 'salt "*" cmd.run "top -bn1 | grep Cpu"',
            'StatusCheckFailed': 'salt "*" service.restart mysql',
            'NetworkIn': 'salt "*" cmd.run "netstat -i"'
        },
        'Custom/MySQL': {
            'Port3306Status': 'salt "*" service.restart mysql && salt "*" cmd.run "systemctl status mysql"'
        }

    }
    
    return salt_commands.get(trigger_namespace, {}).get(trigger_metric_name, 'salt "*" cmd.run "echo No command found"')

def call_bedrock_ai(pk_id, salt_command, arg, act_id):
    """Bedrock AI 호출"""
    try:
        bearer_token = os.getenv('AWS_BEARER_TOKEN_BEDROCK')
        if not bearer_token:
            return "AI analysis failed: AWS_BEARER_TOKEN_BEDROCK not configured"
        
        prompt = f"""
        너는 이벤트 알람 분석 전문가이고 한국어 기반으로 답변 해줘. 그리고 아래 양식에 맞춰 대답해주면 좋겠어.
        System Alert Analysis Request:
        
        PK ID: {pk_id}
        Salt Command: {salt_command}
        Argument: {arg}
        Activity ID: {act_id}
        
        Please analyze this system alert and provide:
        1. Root cause analysis
        2. Recommended actions
        3. Prevention measures
        
        Respond in JSON format with keys: analysis, actions, prevention
        """
        
        headers = {
            'Authorization': f'Bearer {bearer_token}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }
        
        url = 'https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.claude-3-sonnet-20240229-v1:0/invoke'
        
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            return result['content'][0]['text']
        else:
            return f"AI analysis failed: HTTP {response.status_code} - {response.text}"
        
    except Exception as e:
        return f"AI analysis failed: {str(e)}"

def get_salt_token():
    """SaltStack API 토큰 획득"""
    try:
        response = requests.post(f'{SALT_API_URL}/login', 
                               json={'username': SALT_USERNAME, 'password': SALT_PASSWORD, 'eauth': 'pam'})
        if response.status_code == 200:
            return response.json()['return'][0]['token']
    except Exception as e:
        print(f"Salt token error: {e}")
    return None

def execute_salt_command(target, function, args=None, timeout=10):
    """SaltStack 명령어 실행"""
    token = get_salt_token()
    if not token:
        return {'error': 'Failed to get Salt token'}
    
    headers = {'X-Auth-Token': token, 'Content-Type': 'application/json'}
    data = {
        'client': 'local',
        'tgt': target,
        'fun': function
    }
    if args:
        data['arg'] = args
    
    try:
        response = requests.post(f'{SALT_API_URL}/', json=data, headers=headers, timeout=timeout)
        if response.status_code == 200:
            return response.json()
        else:
            return {'error': f'Command failed with status {response.status_code}'}
    except requests.exceptions.Timeout:
        return {'error': f'Request timeout after {timeout} seconds'}
    except Exception as e:
        return {'error': str(e)}

db = SQLAlchemy(app)
api = Api(app, doc='/swagger/', title='Rescue Bot API', version='1.0', description='CloudWatch Alarm Events API')

# Namespaces
batch_ns = api.namespace('batch', description='Batch processing operations')
scheduler_ns = api.namespace('scheduler', description='Scheduler management')
db_ns = api.namespace('db', description='Database operations')
salt_ns = api.namespace('salt', description='Salt command operations')
test_ns = api.namespace('test', description='Test operations')

# Models
class CloudwatchAlarmEvent(db.Model):
    __tablename__ = 'cloudwatch_alarm_events'
    
    id = db.Column(db.BigInteger, primary_key=True)
    alarm_name = db.Column(db.String(255))
    alarm_description = db.Column(db.Text)
    aws_account_id = db.Column(db.String(20))
    new_state_value = db.Column(db.String(50))
    new_state_reason = db.Column(db.Text)
    state_change_time = db.Column(db.DateTime)
    region = db.Column(db.String(100))
    trigger_metric_name = db.Column(db.String(100))
    trigger_namespace = db.Column(db.String(100))
    trigger_statistic_type = db.Column(db.String(50))
    trigger_statistic = db.Column(db.String(50))
    trigger_unit = db.Column(db.String(50))
    trigger_period = db.Column(db.Integer)
    trigger_evaluation_periods = db.Column(db.Integer)
    trigger_comparison_operator = db.Column(db.String(100))
    trigger_threshold = db.Column(db.Float)
    trigger_treat_missing_data = db.Column(db.String(100))
    trigger_instance_id = db.Column(db.String(50))
    raw_message = db.Column(db.JSON)

class CloudwatchAlarmMetrics(db.Model):
    __tablename__ = 'cloudwatch_alarm_metrics'
    
    id = db.Column(db.BigInteger, primary_key=True)
    alarm_description = db.Column(db.Text)
    metric_id = db.Column(db.String(100))
    metric_host = db.Column(db.String(100))
    metric_pattern = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class BatchStatus(db.Model):
    __tablename__ = 'batch_status'
    
    id = db.Column(db.Integer, primary_key=True)
    last_processed_id = db.Column(db.BigInteger, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class BedrockResponse(db.Model):
    __tablename__ = 'bedrock_responses'
    
    id = db.Column(db.BigInteger, primary_key=True)
    metric_id = db.Column(db.BigInteger)
    salt_command = db.Column(db.Text)
    ai_request = db.Column(db.Text)
    ai_response = db.Column(db.Text)
    response_time_ms = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Swagger Models
salt_command_model = api.model('SaltCommand', {
    'target': fields.String(required=True, description='Target minion'),
    'function': fields.String(required=True, description='Salt function'),
    'args': fields.List(fields.String, description='Function arguments')
})

cloudwatch_alarm_event_model = api.model('CloudwatchAlarmEvent', {
    'id': fields.Integer(description='Event ID'),
    'alarm_name': fields.String(description='Alarm name'),
    'alarm_description': fields.String(description='Alarm description'),
    'aws_account_id': fields.String(description='AWS Account ID'),
    'new_state_value': fields.String(description='New state value'),
    'new_state_reason': fields.String(description='New state reason'),
    'state_change_time': fields.String(description='State change time'),
    'region': fields.String(description='AWS Region'),
    'trigger_metric_name': fields.String(description='Trigger metric name'),
    'trigger_namespace': fields.String(description='Trigger namespace'),
    'trigger_instance_id': fields.String(description='Trigger instance ID'),
    'trigger_threshold': fields.Float(description='Trigger threshold')
})

cloudwatch_alarm_metrics_model = api.model('CloudwatchAlarmMetrics', {
    'id': fields.Integer(description='Metric ID'),
    'alarm_description': fields.String(description='Alarm description'),
    'metric_id': fields.String(description='Metric ID'),
    'metric_host': fields.String(description='Metric host'),
    'metric_pattern': fields.String(description='Metric pattern'),
    'created_at': fields.String(description='Created timestamp')
})

batch_status_model = api.model('BatchStatus', {
    'id': fields.Integer(description='Status ID'),
    'last_processed_id': fields.Integer(description='Last processed ID'),
    'updated_at': fields.String(description='Updated timestamp')
})

bedrock_response_model = api.model('BedrockResponse', {
    'id': fields.Integer(description='Response ID'),
    'metric_id': fields.Integer(description='Metric ID'),
    'salt_command': fields.String(description='Salt command'),
    'ai_request': fields.String(description='AI request'),
    'ai_response': fields.String(description='AI response'),
    'response_time_ms': fields.Integer(description='Response time in milliseconds'),
    'created_at': fields.String(description='Created timestamp')
})

process_single_model = api.model('ProcessSingle', {
    'id': fields.Integer(required=True, description='Record ID to process')
})

slack_test_model = api.model('SlackTest', {
    'message': fields.String(required=True, description='Test message to send')
})

# Salt API Routes
@salt_ns.route('/minions')
class SaltMinions(Resource):
    def get(self):
        """Get all Salt minions with detailed info"""
        try:
            grains_result = execute_salt_command('*', 'grains.items', timeout=10)
            if 'error' in grains_result:
                return grains_result, 500
            
            grains_data = grains_result.get('return', [{}])[0] if grains_result.get('return') else {}
            
            minion_info = []
            for minion_id, grains in grains_data.items():
                minion_data = {
                    'minion_id': minion_id,
                    'fqdn': grains.get('fqdn', 'unknown'),
                    'host': grains.get('host', 'unknown'),
                    'ip4_interfaces': grains.get('ip4_interfaces', {}),
                    'os': grains.get('os', 'unknown'),
                    'status': 'online'
                }
                minion_info.append(minion_data)
            
            return {'minions': minion_info}
        except Exception as e:
            return {'error': str(e)}, 500

@salt_ns.route('/minions/fqdn/<fqdn>/id')
class SaltMinionIdByFQDN(Resource):
    def get(self, fqdn):
        """Get minion ID by FQDN"""
        try:
            grains_result = execute_salt_command('*', 'grains.items', timeout=10)
            if 'error' in grains_result:
                return grains_result, 500
            
            grains_data = grains_result.get('return', [{}])[0] if grains_result.get('return') else {}
            
            for minion_id, grains in grains_data.items():
                minion_fqdn = grains.get('fqdn', '')
                if minion_fqdn == fqdn:
                    return {
                        'fqdn': fqdn,
                        'minion_id': minion_id,
                        'host': grains.get('host', 'unknown'),
                        'ip_addresses': grains.get('ip4_interfaces', {})
                    }
            
            return {'error': f'No minion found with FQDN: {fqdn}'}, 404
        except Exception as e:
            return {'error': str(e)}, 500

@salt_ns.route('/execute')
class SaltExecute(Resource):
    @api.expect(salt_command_model)
    def post(self):
        """Execute Salt command"""
        data = request.get_json()
        target = data.get('target', '*')
        function = data.get('function')
        args = data.get('args', [])
        
        if not function:
            return {'error': 'Function is required'}, 400
        
        result = execute_salt_command(target, function, args)
        
        # Save execution log to database
        try:
            connection = get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO salt_execution_logs (target_minion, function_name, arguments, result, executed_at) VALUES (%s, %s, %s, %s, %s)",
                    (target, function, json.dumps(args), json.dumps(result), datetime.utcnow())
                )
                connection.commit()
            connection.close()
        except Exception as e:
            print(f"Failed to save execution log: {e}")
        
        return result

@salt_ns.route('/jobs')
class SaltJobs(Resource):
    def get(self):
        """Get Salt jobs using simple approach"""
        try:
            running_result = execute_salt_command('*', 'saltutil.running', timeout=5)
            if 'error' in running_result:
                jobs_data = {}
            else:
                jobs_data = running_result.get('return', [{}])[0] if running_result.get('return') else {}
            
            running_jobs = []
            for minion_id, jobs in jobs_data.items():
                if jobs:
                    for job in jobs:
                        running_jobs.append({
                            'jid': job.get('jid'),
                            'minion_id': minion_id,
                            'function': job.get('fun'),
                            'target': job.get('tgt'),
                            'status': 'running',
                            'pid': job.get('pid')
                        })
            
            return {
                'running_jobs': running_jobs,
                'running_count': len(running_jobs)
            }
        except Exception as e:
            return {'error': str(e)}, 500

@salt_ns.route('/jobs/active')
class SaltActiveJobs(Resource):
    @api.param('timeout', 'Request timeout in seconds', type=int, default=5)
    def get(self):
        """Get currently running Salt jobs with timeout control"""
        timeout = request.args.get('timeout', 5, type=int)
        
        try:
            result = execute_salt_command('*', 'saltutil.running', timeout=timeout)
            if 'error' in result:
                return {
                    'error': result['error'],
                    'running_jobs': [],
                    'minions_status': {},
                    'count': 0,
                    'timeout_used': timeout
                }, 500
            
            active_jobs = []
            jobs_data = result.get('return', [{}])[0] if result.get('return') else {}
            
            for minion_id, jobs in jobs_data.items():
                if jobs:
                    for job in jobs:
                        active_jobs.append({
                            'minion_id': minion_id,
                            'jid': job.get('jid'),
                            'fun': job.get('fun'),
                            'pid': job.get('pid'),
                            'tgt': job.get('tgt'),
                            'tgt_type': job.get('tgt_type')
                        })
            
            return {
                'running_jobs': active_jobs,
                'minions_status': jobs_data,
                'count': len(active_jobs),
                'timeout_used': timeout
            }
        except Exception as e:
            return {
                'error': str(e),
                'running_jobs': [],
                'minions_status': {},
                'count': 0,
                'timeout_used': timeout
            }, 500

# Batch/Scheduler/DB Routes (seongdong branch priority)
@batch_ns.route('/process-new-data')
class ProcessNewData(Resource):
    def post(self):
        """새로운 데이터만 배치 처리"""
        try:
            connection = get_db_connection()
            
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT last_processed_id FROM batch_status WHERE id = 1")
                batch_status = cursor.fetchone()
                
                if not batch_status:
                    cursor.execute("INSERT INTO batch_status (id, last_processed_id) VALUES (1, 0)")
                    connection.commit()
                    last_processed_id = 0
                else:
                    last_processed_id = batch_status['last_processed_id']
                
                cursor.execute("""
                    SELECT * FROM cloudwatch_alarm_metrics 
                    WHERE id > %s 
                    ORDER BY id ASC
                """, (last_processed_id,))
                
                new_records = cursor.fetchall()
                
                if not new_records:
                    connection.close()
                    return {
                        'status': 'no_new_data',
                        'message': 'No new records to process',
                        'last_processed_id': last_processed_id
                    }
                
                processed_results = []
                new_last_id = last_processed_id
                
                print(f"Processing {len(new_records)} new records starting from ID > {last_processed_id}")
                for record in new_records:
                    # alarm_description 기반으로 먼저 확인
                    print(f"Processing record ID: {record['id']} with description: {record.get('alarm_description')}")
                    salt_command, exec_result = get_salt_command_by_description(
                        record.get('alarm_description'), 
                        record.get('metric_host')
                    )

                    print(f"Determined salt_command: {salt_command}")
                    
                    # 매칭되지 않으면 기존 방식 사용
                    if not salt_command:
                        salt_command = get_salt_command(
                            record.get('metric_host'),
                            record.get('metric_pattern')
                        )
                        exec_result = None
                    
                    ai_response = call_bedrock_ai(
                        pk_id=record['id'],
                        salt_command=salt_command,
                        arg=record.get('metric_host'),
                        act_id=record.get('metric_id')
                    )
                    
                    # Slack으로 AI 분석 결과 전송
                    slack_message = f"""🤖 *AI 분석 완료*
📊 *Metric ID*: {record.get('metric_id')}
🏠 *Host*: {record.get('metric_host')}
⚡ *Salt Command*: `{salt_command}`

📋 *AI 분석 결과*:
```
{ai_response[:500]}{'...' if len(ai_response) > 500 else ''}
```
⏰ *처리 시간*: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                    
                    send_slack_notification(slack_message)
                    
                    processed_results.append({
                        'id': record['id'],
                        'metric_id': record.get('metric_id'),
                        'salt_command': salt_command,
                        'ai_response': ai_response,
                        'processed_at': datetime.now().isoformat()
                    })
                    
                    new_last_id = record['id']
                
                cursor.execute("""
                    UPDATE batch_status 
                    SET last_processed_id = %s, updated_at = NOW() 
                    WHERE id = 1
                """, (new_last_id,))
                connection.commit()
            
            connection.close()
            
            return {
                'status': 'success',
                'processed_count': len(processed_results),
                'last_processed_id': new_last_id,
                'results': processed_results
            }
            
        except Exception as e:
            return {'error': str(e)}, 500
        
@batch_ns.route('/process-single')
class ProcessSingleRecord(Resource):
    def post(self):
        """단일 레코드 처리"""
        try:
            data = request.get_json()
            record_id = data.get('id')
            if not record_id:
                return {'error': 'ID is required'}, 400
            connection = get_db_connection()
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT * FROM cloudwatch_alarm_metrics WHERE id = %s
                """, (record_id,))
                record = cursor.fetchone()
                if not record:
                    connection.close()
                    return {'error': 'Record not found'}, 404

                print(f"Processing single record ID: {record['id']} with description: {record.get('alarm_description')}")    
                salt_command, exec_result = get_salt_command_by_description(
                    record.get('alarm_description'),
                    record.get('metric_host')
                )
                
                if not salt_command:
                    salt_command = get_salt_command(
                        record.get('metric_host'),
                        record.get('metric_pattern')
                    )
                    exec_result = None
            
                print(f"Determined salt_command: {salt_command}")
                print(f"Exec result: {exec_result}")
                print(f"Record details: {record}")

                start_time = datetime.now()
                ai_response = call_bedrock_ai(
                    pk_id=record['id'],
                    salt_command=str(salt_command),
                    arg=record.get('metric_host'),
                    act_id=record.get('metric_id')
                )
                response_time = int((datetime.now() - start_time).total_seconds() * 1000)
                # Bedrock 응답 저장
                response_time = int((datetime.now() - start_time).total_seconds() * 1000)
                # Bedrock 응답 저장
                cursor.execute("""
                    INSERT INTO bedrock_responses
                    (metric_id, salt_command, ai_request, ai_response, response_time_ms)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    record['id'],
                    str(salt_command),
                    f"PK ID: {record['id']}, Salt Command: {str(salt_command)}, Argument: {record.get('metric_host')}, Activity ID: {record.get('metric_id')}",
                    ai_response,
                    response_time
                ))
                connection.commit()
                # 배치 상태 업데이트 (현재 레코드 ID가 마지막 처리 ID보다 크면)
                cursor.execute("SELECT last_processed_id FROM batch_status WHERE id = 1")
                batch_status = cursor.fetchone()
                if batch_status and record['id'] > batch_status['last_processed_id']:
                    cursor.execute("""
                        UPDATE batch_status
                        SET last_processed_id = %s, updated_at = NOW()
                        WHERE id = 1
                    """, (record['id'],))
                    connection.commit()
            connection.close()

            send_slack_notification(f"""🔍 *단일 레코드 AI 분석 완료*
📊 *ID*: {record['id']}
🏠 *Host*: {record.get('metric_host')}
⚡ *Salt Command*: `{str(salt_command)}`
📋 *AI Response*: {str(ai_response)[:1000]}{'...' if len(str(ai_response)) > 1000 else ''}
⏰ *처리 시간*: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}""")
            print(f"Single record processed successfully: {record['id']}")
            return {
                'status': 'success',
                'id': record['id'],
                'metric_id': record.get('metric_id'),
                'salt_command': str(salt_command),
                'ai_response': ai_response,
                'processed_at': datetime.now().isoformat()
            }
        except Exception as e:
            return {'error': str(e)}, 500
        
@batch_ns.route('/status')
class BatchStatusResource(Resource):
    def get(self):
        """배치 상태 조회"""
        try:
            connection = get_db_connection()
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT * FROM batch_status WHERE id = 1")
                status = cursor.fetchone()
                if not status:
                    # 배치 상태 레코드가 없으면 생성
                    cursor.execute("INSERT INTO batch_status (id, last_processed_id) VALUES (1, 0)")
                    connection.commit()
                    status = {'id': 1, 'last_processed_id': 0, 'updated_at': datetime.now()}
            connection.close()
            # datetime 객체를 문자열로 변환
            if isinstance(status.get('updated_at'), datetime):
                status['updated_at'] = status['updated_at'].isoformat()
            return {
                'status': 'success',
                'batch_status': status
            }
        except Exception as e:
            return {'error': str(e)}, 500

@batch_ns.route('/process-single')
class ProcessSingleRecord(Resource):
    @api.expect(process_single_model)
    def post(self):
        """단일 레코드 처리"""
        try:
            data = request.get_json()
            record_id = data.get('id')
            
            if not record_id:
                return {'error': 'ID is required'}, 400
            
            connection = get_db_connection()
            
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT * FROM cloudwatch_alarm_metrics WHERE id = %s
                """, (record_id,))
                
                record = cursor.fetchone()
                
                if not record:
                    connection.close()
                    return {'error': 'Record not found'}, 404
                
                salt_command = get_salt_command(
                    record.get('metric_host'),
                    record.get('metric_pattern')
                )

                
                
                start_time = datetime.now()
                ai_response = call_bedrock_ai(
                    pk_id=record['id'],
                    salt_command=salt_command,
                    arg=record.get('metric_host'),
                    act_id=record.get('metric_id')
                )
                response_time = int((datetime.now() - start_time).total_seconds() * 1000)
                
                # Slack으로 AI 분석 결과 전송
                slack_message = f"""🔍 *단일 레코드 AI 분석 완료*
📊 *Record ID*: {record['id']}
🏠 *Host*: {record.get('metric_host')}
⚡ *Salt Command*: `{salt_command}`
⏱️ *응답 시간*: {response_time}ms

📋 *AI 분석 결과*:
```
{ai_response[:1000]}{'...' if len(ai_response) > 1000 else ''}
```
⏰ *처리 시간*: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                send_slack_notification(slack_message)
                
                # Bedrock 응답 저장
                cursor.execute("""
                    INSERT INTO bedrock_responses 
                    (metric_id, salt_command, ai_request, ai_response, response_time_ms) 
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    record['id'],
                    salt_command,
                    f"PK ID: {record['id']}, Salt Command: {salt_command}",
                    ai_response,
                    response_time
                ))
                connection.commit()
            
            connection.close()
            
            return {
                'status': 'success',
                'id': record['id'],
                'metric_id': record.get('metric_id'),
                'salt_command': salt_command,
                'ai_response': ai_response,
                'processed_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {'error': str(e)}, 500

@batch_ns.route('/status')
class BatchStatusResource(Resource):
    @api.marshal_with(batch_status_model)
    def get(self):
        """배치 상태 조회"""
        try:
            connection = get_db_connection()
            
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT * FROM batch_status WHERE id = 1")
                status = cursor.fetchone()
                
                if not status:
                    cursor.execute("INSERT INTO batch_status (id, last_processed_id) VALUES (1, 0)")
                    connection.commit()
                    status = {'id': 1, 'last_processed_id': 0, 'updated_at': datetime.now()}
            
            connection.close()
            
            if isinstance(status.get('updated_at'), datetime):
                status['updated_at'] = status['updated_at'].isoformat()
            
            return {
                'status': 'success',
                'batch_status': status
            }
            
        except Exception as e:
            return {'error': str(e)}, 500

@scheduler_ns.route('/start')
class StartScheduler(Resource):
    def post(self):
        """배치 스케줄러 시작"""
        try:
            if batch_scheduler:
                batch_scheduler.start()
                return {'status': 'success', 'message': 'Batch scheduler started'}
            else:
                return {'error': 'Batch scheduler not available'}, 500
        except Exception as e:
            return {'error': str(e)}, 500

@scheduler_ns.route('/stop')
class StopScheduler(Resource):
    def post(self):
        """배치 스케줄러 중지"""
        try:
            if batch_scheduler:
                batch_scheduler.stop()
                return {'status': 'success', 'message': 'Batch scheduler stopped'}
            else:
                return {'error': 'Batch scheduler not available'}, 500
        except Exception as e:
            return {'error': str(e)}, 500

@scheduler_ns.route('/status')
class SchedulerStatus(Resource):
    def get(self):
        """스케줄러 상태 조회"""
        try:
            if batch_scheduler:
                return {
                    'status': 'success',
                    'scheduler_running': batch_scheduler.running
                }
            else:
                return {
                    'status': 'error',
                    'message': 'Batch scheduler not available'
                }
        except Exception as e:
            return {'error': str(e)}, 500

@db_ns.route('/cloudwatch-events')
class CloudwatchEvents(Resource):
    @api.marshal_list_with(cloudwatch_alarm_event_model)
    @api.param('page', 'Page number', type=int, default=1)
    @api.param('per_page', 'Items per page', type=int, default=50)
    def get(self):
        """CloudWatch 알람 이벤트 조회"""
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        
        events = CloudwatchAlarmEvent.query.order_by(CloudwatchAlarmEvent.state_change_time.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return {
            'events': [{
                'id': e.id,
                'alarm_name': e.alarm_name,
                'alarm_description': e.alarm_description,
                'aws_account_id': e.aws_account_id,
                'new_state_value': e.new_state_value,
                'new_state_reason': e.new_state_reason,
                'state_change_time': e.state_change_time.isoformat() if e.state_change_time else None,
                'region': e.region,
                'trigger_metric_name': e.trigger_metric_name,
                'trigger_namespace': e.trigger_namespace,
                'trigger_instance_id': e.trigger_instance_id,
                'trigger_threshold': e.trigger_threshold
            } for e in events.items],
            'total': events.total,
            'pages': events.pages,
            'current_page': page
        }

@db_ns.route('/alarm-metrics')
class AlarmMetrics(Resource):
    @api.marshal_list_with(cloudwatch_alarm_metrics_model)
    def get(self):
        """CloudWatch 알람 메트릭 조회"""
        metrics = CloudwatchAlarmMetrics.query.order_by(CloudwatchAlarmMetrics.created_at.desc()).limit(100).all()
        
        return [{
            'id': m.id,
            'alarm_description': m.alarm_description,
            'metric_id': m.metric_id,
            'metric_host': m.metric_host,
            'metric_pattern': m.metric_pattern,
            'created_at': m.created_at.isoformat() if m.created_at else None
        } for m in metrics]

@db_ns.route('/bedrock-responses')
class BedrockResponses(Resource):
    @api.marshal_list_with(bedrock_response_model)
    @api.param('page', 'Page number', type=int, default=1)
    @api.param('per_page', 'Items per page', type=int, default=20)
    def get(self):
        """Bedrock AI 응답 조회"""
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        responses = BedrockResponse.query.order_by(BedrockResponse.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return {
            'responses': [{
                'id': r.id,
                'metric_id': r.metric_id,
                'salt_command': r.salt_command,
                'ai_request': r.ai_request,
                'ai_response': r.ai_response,
                'response_time_ms': r.response_time_ms,
                'created_at': r.created_at.isoformat() if r.created_at else None
            } for r in responses.items],
            'total': responses.total,
            'pages': responses.pages,
            'current_page': page
        }

@db_ns.route('/all-data')
class AllDatabaseData(Resource):
    def get(self):
        """모든 데이터베이스 정보 조회"""
        try:
            connection = get_db_connection()
            result = {}
            
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SHOW TABLES")
                tables = [row[f'Tables_in_{DB_NAME}'] for row in cursor.fetchall()]
                
                for table in tables:
                    cursor.execute(f"SELECT * FROM {table}")
                    table_data = cursor.fetchall()
                    
                    for row in table_data:
                        for key, value in row.items():
                            if isinstance(value, datetime):
                                row[key] = value.isoformat()
                    
                    result[table] = {
                        'data': table_data
                    }
            
            connection.close()
            return result
            
        except Exception as e:
            return {'error': str(e)}, 500

@test_ns.route('/bedrock')
class BedrockTest(Resource):
    def get(self):
        """Bedrock 연결 테스트"""
        try:
            bearer_token = os.getenv('AWS_BEARER_TOKEN_BEDROCK')
            if not bearer_token:
                return {
                    'error': 'AWS_BEARER_TOKEN_BEDROCK not configured',
                    'status': 'token_missing'
                }, 500
            
            headers = {
                'Authorization': f'Bearer {bearer_token}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "messages": [
                    {
                        "role": "user",
                        "content": "Hello, test connection"
                    }
                ]
            }
            
            url = 'https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.claude-3-sonnet-20240229-v1:0/invoke'
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                return {
                    'status': 'success',
                    'response': result['content'][0]['text']
                }
            else:
                return {
                    'error': f'HTTP {response.status_code} - {response.text}',
                    'status': 'api_error'
                }, 500
            
        except Exception as e:
            return {'error': str(e)}, 500

@test_ns.route('/slack')
class SlackTest(Resource):
    @api.expect(slack_test_model)
    def post(self):
        """Slack 전송 테스트"""
        data = request.get_json()
        message = data.get('message', '🤖 테스트 메시지입니다!')
        
        test_message = f"""🧪 *Slack 연동 테스트*
📅 *시간*: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💬 *메시지*: {message}
✅ *상태*: 정상 작동 중"""
        
        success = send_slack_notification(test_message)
        
        if success:
            return {
                'status': 'success',
                'message': 'Slack notification sent successfully',
                'webhook_url': SLACK_WEBHOOK_URL[:50] + '...',
                'sent_at': datetime.now().isoformat()
            }
        else:
            return {
                'status': 'failed',
                'message': 'Failed to send Slack notification',
                'webhook_url': SLACK_WEBHOOK_URL[:50] + '...' if SLACK_WEBHOOK_URL else 'Not configured'
            }, 500

@test_ns.route('/slack/ai-sample')
class SlackAISampleTest(Resource):
    def post(self):
        """AI 분석 결과 샘플 Slack 전송 테스트"""
        sample_message = f"""🤖 *AI 분석 완료*
📊 *Metric ID*: sample-metric-001
🏠 *Host*: test-server.example.com
⚡ *Salt Command*: `salt "*" service.restart mysql`
⏱️ *응답 시간*: 1250ms

📋 *AI 분석 결과*:
```
시스템 분석 결과:
1. 근본 원인: MySQL 서비스가 예상치 못하게 중단됨
2. 권장 조치: 서비스 재시작 및 로그 확인 필요
3. 예방 조치: 정기적인 헬스체크 설정 권장
```
⏰ *처리 시간*: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        success = send_slack_notification(sample_message)
        
        if success:
            return {
                'status': 'success',
                'message': 'AI sample notification sent successfully',
                'sample_type': 'AI Analysis Result'
            }
        else:
            return {
                'status': 'failed',
                'message': 'Failed to send AI sample notification'
            }, 500

@test_ns.route('/db')
class DatabaseTest(Resource):
    def get(self):
        """데이터베이스 연결 테스트"""
        try:
            connection = get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT VERSION()")
                version = cursor.fetchone()[0]
                cursor.execute("SHOW TABLES")
                tables = cursor.fetchall()
                
                # Create salt_execution_logs table if not exists
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS salt_execution_logs (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        target_minion VARCHAR(255),
                        function_name VARCHAR(255),
                        arguments JSON,
                        result JSON,
                        executed_at DATETIME
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                connection.commit()
            
            connection.close()
            return {
                'status': 'success',
                'mysql_version': version,
                'tables': [table[0] for table in tables]
            }
        except Exception as e:
            return {'error': str(e)}, 500

@api.route('/health')
class HealthCheck(Resource):
    def get(self):
        """Health check endpoint"""
        return {
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat()
        }

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    # 앱 시작 시 스케줄러 자동 시작 (seongdong branch priority)
    if batch_scheduler:
        batch_scheduler.start()
    
    try:
        app.run(host='0.0.0.0', port=3000, debug=True)
    finally:
        # 앱 종료 시 스케줄러 중지
        if batch_scheduler:
            batch_scheduler.stop()