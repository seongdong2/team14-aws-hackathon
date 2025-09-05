# Rescue Bot Infrastructure as Code

## 개요
Rescue Bot 프로젝트의 AWS 인프라를 Terraform으로 관리합니다.

## 아키텍처
- **VPC**: 10.0.0.0/16 CIDR
- **서브넷**: Public(ALB용), Private(EC2, RDS용)
- **컴퓨팅**: EC2 Auto Scaling Group + Application Load Balancer
- **데이터베이스**: RDS MySQL (Multi-AZ)
- **모니터링**: CloudWatch + EventBridge + Lambda
- **알림**: SNS + Slack 연동
- **AI**: Bedrock Claude-3 Sonnet

## 배포 방법

### 1. 사전 준비
```bash
# AWS CLI 설정
aws configure

# Terraform 설치 확인
terraform version
```

### 2. 변수 설정
```bash
cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars 파일 수정
```

### 3. 인프라 배포
```bash
# 초기화
terraform init

# 계획 확인
terraform plan

# 배포
terraform apply
```

### 4. 리소스 삭제
```bash
terraform destroy
```

## 주요 리소스
- **VPC 및 네트워킹**: VPC, 서브넷, IGW, NAT Gateway
- **보안**: Security Groups, IAM 역할
- **컴퓨팅**: EC2 Launch Template, Auto Scaling Group, ALB
- **데이터베이스**: RDS MySQL, Secrets Manager
- **모니터링**: CloudWatch Alarms, EventBridge Rules
- **서버리스**: Lambda Functions (Alert Handler, Bedrock)
- **알림**: SNS Topic

## 환경별 설정
- **개발환경**: t3.micro, 단일 AZ, 최소 리소스
- **프로덕션**: t3.medium, Multi-AZ, 고가용성

## 모니터링 설정
- MySQL 포트 3306 상태 모니터링
- CloudWatch Agent를 통한 시스템 메트릭 수집
- EventBridge를 통한 이벤트 기반 자동화
- Bedrock Claude를 통한 AI 기반 문제 해결