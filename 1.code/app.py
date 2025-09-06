from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_restx import Api, Resource, fields
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pymysql
import boto3
import json
import os
import requests

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

def get_db_connection():
    """데이터베이스 연결 생성"""
    return pymysql.connect(**DB_CONFIG)

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

# Model
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

# Swagger Models
alarm_event_model = api.model('AlarmEvent', {
    'id': fields.Integer(description='Event ID'),
    'alarm_name': fields.String(description='Alarm name'),
    'new_state_value': fields.String(description='New state value'),
    'state_change_time': fields.String(description='State change time'),
    'trigger_instance_id': fields.String(description='Instance ID'),
    'region': fields.String(description='AWS Region')
})

webhook_model = api.model('Webhook', {
    'alarm_name': fields.String(required=True, description='Alarm name'),
    'new_state_value': fields.String(required=True, description='New state value'),
    'trigger_instance_id': fields.String(description='Instance ID')
})

salt_command_model = api.model('SaltCommand', {
    'target': fields.String(required=True, description='Target minion'),
    'function': fields.String(required=True, description='Salt function'),
    'args': fields.List(fields.String, description='Function arguments')
})

@api.route('/api/alarms')
class AlarmList(Resource):
    @api.marshal_list_with(alarm_event_model)
    @api.param('page', 'Page number', type=int, default=1)
    @api.param('per_page', 'Items per page', type=int, default=50)
    @api.param('state', 'Alarm state filter')
    @api.param('instance_id', 'Instance ID filter')
    def get(self):
        """Get alarm events with pagination and filtering"""
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        state = request.args.get('state')
        instance_id = request.args.get('instance_id')
        
        query = CloudwatchAlarmEvent.query
        
        if state:
            query = query.filter(CloudwatchAlarmEvent.new_state_value == state)
        if instance_id:
            query = query.filter(CloudwatchAlarmEvent.trigger_instance_id == instance_id)
        
        events = query.order_by(CloudwatchAlarmEvent.state_change_time.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return {
            'events': [{
                'id': e.id,
                'alarm_name': e.alarm_name,
                'alarm_description': e.alarm_description,
                'new_state_value': e.new_state_value,
                'new_state_reason': e.new_state_reason,
                'state_change_time': e.state_change_time.isoformat() if e.state_change_time else None,
                'region': e.region,
                'trigger_instance_id': e.trigger_instance_id,
                'trigger_metric_name': e.trigger_metric_name
            } for e in events.items],
            'total': events.total,
            'pages': events.pages,
            'current_page': page
        }

@api.route('/api/alarms/<int:alarm_id>')
class AlarmDetail(Resource):
    def get(self, alarm_id):
        """Get alarm event detail"""
        event = CloudwatchAlarmEvent.query.get_or_404(alarm_id)
        
        return {
            'id': event.id,
            'alarm_name': event.alarm_name,
            'alarm_description': event.alarm_description,
            'aws_account_id': event.aws_account_id,
            'new_state_value': event.new_state_value,
            'new_state_reason': event.new_state_reason,
            'state_change_time': event.state_change_time.isoformat() if event.state_change_time else None,
            'region': event.region,
            'trigger_metric_name': event.trigger_metric_name,
            'trigger_namespace': event.trigger_namespace,
            'trigger_threshold': event.trigger_threshold,
            'trigger_instance_id': event.trigger_instance_id,
            'raw_message': event.raw_message
        }

@api.route('/api/dashboard/stats')
class DashboardStats(Resource):
    def get(self):
        """Get dashboard statistics"""
        today = datetime.utcnow().date()
        
        total_today = CloudwatchAlarmEvent.query.filter(
            db.func.date(CloudwatchAlarmEvent.state_change_time) == today
        ).count()
        
        alarm_today = CloudwatchAlarmEvent.query.filter(
            db.func.date(CloudwatchAlarmEvent.state_change_time) == today,
            CloudwatchAlarmEvent.new_state_value == 'ALARM'
        ).count()
        
        ok_today = CloudwatchAlarmEvent.query.filter(
            db.func.date(CloudwatchAlarmEvent.state_change_time) == today,
            CloudwatchAlarmEvent.new_state_value == 'OK'
        ).count()
        
        return {
            'total_events_today': total_today,
            'alarm_events_today': alarm_today,
            'ok_events_today': ok_today,
            'alarm_rate': round((alarm_today / total_today * 100) if total_today > 0 else 0, 2)
        }

@api.route('/api/alarms/instance/<instance_id>')
class InstanceAlarms(Resource):
    def get(self, instance_id):
        """Get alarms for specific instance"""
        events = CloudwatchAlarmEvent.query.filter_by(trigger_instance_id=instance_id)\
            .order_by(CloudwatchAlarmEvent.state_change_time.desc()).limit(20).all()
        
        return {
            'instance_id': instance_id,
            'events': [{
                'id': e.id,
                'alarm_name': e.alarm_name,
                'new_state_value': e.new_state_value,
                'state_change_time': e.state_change_time.isoformat() if e.state_change_time else None
            } for e in events]
        }

@api.route('/webhook/cloudwatch')
class CloudWatchWebhook(Resource):
    @api.expect(webhook_model)
    def post(self):
        """Receive CloudWatch alarm webhook"""
        data = request.get_json()
        
        # 중복 이벤트 체크 (5분 내)
        five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
        existing = CloudwatchAlarmEvent.query.filter(
            CloudwatchAlarmEvent.trigger_instance_id == data.get('trigger_instance_id'),
            CloudwatchAlarmEvent.state_change_time >= five_minutes_ago,
            CloudwatchAlarmEvent.alarm_name == data.get('alarm_name')
        ).first()
        
        if existing:
            return {'message': 'Duplicate event ignored'}, 200
        
        # 새 이벤트 생성
        event = CloudwatchAlarmEvent(
            alarm_name=data.get('alarm_name'),
            alarm_description=data.get('alarm_description'),
            aws_account_id=data.get('aws_account_id'),
            new_state_value=data.get('new_state_value'),
            new_state_reason=data.get('new_state_reason'),
            state_change_time=datetime.utcnow(),
            region=data.get('region'),
            trigger_metric_name=data.get('trigger_metric_name'),
            trigger_namespace=data.get('trigger_namespace'),
            trigger_instance_id=data.get('trigger_instance_id'),
            raw_message=data
        )
        
        db.session.add(event)
        db.session.commit()
        
        return {
            'status': 'success',
            'event_id': event.id
        }

@api.route('/api/salt/minions')
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

@api.route('/api/salt/minions/fqdn/<fqdn>/id')
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

@api.route('/api/salt/execute')
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

@api.route('/api/salt/services/<service_name>')
class SaltServiceControl(Resource):
    @api.param('action', 'Service action (start/stop/restart/status)', required=True)
    @api.param('target', 'Target minion', default='*')
    def post(self, service_name):
        """Control service on minions"""
        action = request.args.get('action')
        target = request.args.get('target', '*')
        
        if action not in ['start', 'stop', 'restart', 'status']:
            return {'error': 'Invalid action'}, 400
        
        function = f'service.{action}'
        result = execute_salt_command(target, function, [service_name])
        return result

@api.route('/api/salt/mysql/restart')
class MySQLRestart(Resource):
    @api.param('target', 'Target minion', default='*')
    def post(self):
        """Restart MySQL service"""
        target = request.args.get('target', '*')
        result = execute_salt_command(target, 'service.restart', ['mysql'])
        return result

@api.route('/api/salt/jobs')
class SaltJobs(Resource):
    def get(self):
        """Get Salt jobs using simple approach"""
        try:
            # Get running jobs first with short timeout
            running_result = execute_salt_command('*', 'saltutil.running', timeout=5)
            if 'error' in running_result:
                print(f"Salt running command error: {running_result['error']}")
                # Continue with empty running jobs if salt command fails
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
            
            # Try to get job history from database logs
            try:
                connection = get_db_connection()
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT target_minion, function_name, executed_at FROM salt_execution_logs ORDER BY executed_at DESC LIMIT 10"
                    )
                    db_logs = cursor.fetchall()
                connection.close()
                
                completed_jobs = []
                for log in db_logs:
                    completed_jobs.append({
                        'minion_id': log[0],
                        'function': log[1],
                        'executed_at': log[2].isoformat() if log[2] else None,
                        'status': 'completed'
                    })
            except Exception as e:
                print(f"Database error: {e}")
                completed_jobs = []
            
            return {
                'running_jobs': running_jobs,
                'recent_completed_jobs': completed_jobs,
                'running_count': len(running_jobs),
                'completed_count': len(completed_jobs),
                'salt_error': running_result.get('error') if 'error' in running_result else None
            }
        except Exception as e:
            return {'error': str(e)}, 500

@api.route('/api/salt/jobs/active')
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
                if jobs:  # If minion has active jobs
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

@api.route('/api/salt/jobs/<jid>')
class SaltJobDetail(Resource):
    def get(self, jid):
        """Get Salt job detail by JID"""
        try:
            # Check if job is still running
            running_result = execute_salt_command('*', 'saltutil.find_job', [jid])
            if 'error' in running_result:
                return running_result, 500
            
            running_info = running_result.get('return', [{}])[0] if running_result.get('return') else {}
            is_running = bool(running_info)
            
            if is_running:
                return {
                    'jid': jid,
                    'status': 'running',
                    'running_info': running_info,
                    'is_running': True
                }
            else:
                return {
                    'jid': jid,
                    'status': 'completed or not found',
                    'running_info': {},
                    'is_running': False,
                    'message': 'Job not found in running jobs. It may have completed or never existed.'
                }
        except Exception as e:
            return {'error': str(e)}, 500

@api.route('/api/salt/jobs/<jid>/kill')
class SaltJobKill(Resource):
    def post(self, jid):
        """Kill Salt job by JID"""
        result = execute_salt_command('*', 'saltutil.kill_job', [jid])
        if 'error' in result:
            return result, 500
        
        kill_results = result.get('return', [{}])[0] if result.get('return') else {}
        
        return {
            'jid': jid,
            'kill_results': kill_results,
            'success': any(kill_results.values()) if kill_results else False
        }

@api.route('/api/salt/logs')
class SaltExecutionLogs(Resource):
    @api.param('page', 'Page number', type=int, default=1)
    @api.param('per_page', 'Items per page', type=int, default=20)
    def get(self):
        """Get Salt execution logs"""
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        offset = (page - 1) * per_page
        
        try:
            connection = get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM salt_execution_logs ORDER BY executed_at DESC LIMIT %s OFFSET %s",
                    (per_page, offset)
                )
                logs = cursor.fetchall()
                
                cursor.execute("SELECT COUNT(*) FROM salt_execution_logs")
                total = cursor.fetchone()[0]
            
            connection.close()
            
            return {
                'logs': [{
                    'id': log[0],
                    'target_minion': log[1],
                    'function_name': log[2],
                    'arguments': json.loads(log[3]) if log[3] else [],
                    'result': json.loads(log[4]) if log[4] else {},
                    'executed_at': log[5].isoformat() if log[5] else None
                } for log in logs],
                'total': total,
                'page': page,
                'per_page': per_page
            }
        except Exception as e:
            return {'error': str(e)}, 500

@api.route('/test-db')
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
        """Health check endpoint"""
        return {
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat()
        }

@api.route('/test-db')
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
            connection.close()
            return {
                'status': 'success',
                'mysql_version': version,
                'tables': [table[0] for table in tables]
            }
        except Exception as e:
            return {'error': str(e)}, 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=4000, debug=True)