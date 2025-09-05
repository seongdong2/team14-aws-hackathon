from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_restx import Api, Resource, fields
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pymysql
import boto3
import json
import os

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

def get_db_connection():
    """데이터베이스 연결 생성"""
    return pymysql.connect(**DB_CONFIG)

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

@api.route('/health')
class HealthCheck(Resource):
    def get(self):
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
    app.run(host='0.0.0.0', port=3000, debug=True)