#!/bin/bash
yum update -y
yum install -y docker

# Start Docker
systemctl start docker
systemctl enable docker
usermod -a -G docker ec2-user

# Install CloudWatch Agent
wget https://s3.amazonaws.com/amazoncloudwatch-agent/amazon_linux/amd64/latest/amazon-cloudwatch-agent.rpm
rpm -U ./amazon-cloudwatch-agent.rpm

# CloudWatch Agent Configuration
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'EOF'
{
  "metrics": {
    "namespace": "CWAgent",
    "metrics_collected": {
      "cpu": {
        "measurement": ["cpu_usage_idle", "cpu_usage_iowait", "cpu_usage_user", "cpu_usage_system"],
        "metrics_collection_interval": 60
      },
      "disk": {
        "measurement": ["used_percent"],
        "metrics_collection_interval": 60,
        "resources": ["*"]
      },
      "mem": {
        "measurement": ["mem_used_percent"],
        "metrics_collection_interval": 60
      },
      "netstat": {
        "measurement": ["tcp_established", "tcp_listen"],
        "metrics_collection_interval": 60
      },
      "procstat": [
        {
          "pattern": "mysqld",
          "measurement": ["cpu_usage", "memory_rss", "pid_count"]
        }
      ]
    }
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/rescuebot.log",
            "log_group_name": "/aws/ec2/rescuebot",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
EOF

# Start CloudWatch Agent
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s

# Install AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
./aws/install

# Create port check script
cat > /opt/check_mysql_port.py << 'EOF'
#!/usr/bin/env python3
import boto3
import socket
import json

def check_port_3306():
    cloudwatch = boto3.client('cloudwatch', region_name='${region}')
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex(('localhost', 3306))
        sock.close()
        
        port_status = 1 if result == 0 else 0
        
        cloudwatch.put_metric_data(
            Namespace='Custom/MySQL',
            MetricData=[
                {
                    'MetricName': 'Port3306Status',
                    'Value': port_status,
                    'Unit': 'Count'
                }
            ]
        )
        print(f"Port 3306 status: {port_status}")
    except Exception as e:
        print(f"Error checking port: {e}")

if __name__ == "__main__":
    check_port_3306()
EOF

chmod +x /opt/check_mysql_port.py

# Add cron job for port monitoring
echo "*/1 * * * * /usr/bin/python3 /opt/check_mysql_port.py >> /var/log/port_check.log 2>&1" | crontab -

# Pull and run Flask app (placeholder)
docker pull nginx:alpine
docker run -d --name rescuebot-app -p 5000:80 nginx:alpine