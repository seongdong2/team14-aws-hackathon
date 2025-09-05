# CRUD 애플리케이션 개발 프롬프트 양식

## 1단계: 프로젝트 기본 정보
```
다음 정보로 CRUD 애플리케이션을 개발해주세요:

**프로젝트명**: rescue bot 
**도메인**: Automation middleware with saltstack 
**기술 스택**: 
- 백엔드: Python/Flask
- 데이터베이스: RDS MySQL
- 프론트엔드: JavaScript (React)
- 컨테이너: Docker (EC2 배포)
```

## 2단계: 데이터 모델 정의
```
**엔티티명 1**: SaltStackCommand
**필수 필드**:
- id: INT (Primary Key) - 명령어 고유 ID
- command_name: VARCHAR(100) - 명령어 이름
- command_script: TEXT - 실행할 스크립트 내용
- target_service: VARCHAR(50) - 대상 서비스 (mysql, apache 등)
- command_type: ENUM('restart', 'status', 'log_check') - 명령어 타입
- created_at: TIMESTAMP - 생성 시간
- updated_at: TIMESTAMP - 수정 시간

**엔티티명 2**: CloudWatchEvent
**필수 필드**:
- id: INT (Primary Key) - 이벤트 고유 ID
- event_source: VARCHAR(100) - 이벤트 소스 (EC2, RDS 등)
- event_type: VARCHAR(50) - 이벤트 타입 (process_down, connection_failed)
- severity: ENUM('info', 'warning', 'critical', 'fatal') - 심각도
- server_id: VARCHAR(50) - 대상 서버 ID
- event_message: TEXT - 이벤트 메시지
- event_time: TIMESTAMP - 이벤트 발생 시간
- status: ENUM('new', 'processing', 'resolved', 'failed') - 처리 상태

**엔티티명 3**: SaltStackEventLog
**필수 필드**:
- id: INT (Primary Key) - 로그 고유 ID
- cloudwatch_event_id: INT (Foreign Key) - CloudWatch 이벤트 ID
- command_id: INT (Foreign Key) - 실행된 명령어 ID
- execution_result: TEXT - 실행 결과
- execution_status: ENUM('success', 'failed', 'timeout') - 실행 상태
- executed_at: TIMESTAMP - 실행 시간
- response_time: INT - 응답 시간 (초)

**엔티티명 4**: RunbookScript
**필수 필드**:
- id: INT (Primary Key) - Runbook 고유 ID
- title: VARCHAR(200) - 제목
- service_type: VARCHAR(50) - 서비스 타입
- error_pattern: VARCHAR(500) - 에러 패턴 매칭
- solution_script: TEXT - 해결 스크립트
- description: TEXT - 설명
- created_by: VARCHAR(50) - 작성자
- created_at: TIMESTAMP - 생성 시간

**관계**:
- CloudWatchEvent 1:N SaltStackEventLog
- SaltStackCommand 1:N SaltStackEventLog
- RunbookScript와 SaltStackCommand는 service_type으로 연관

**제약조건**:
- command_name은 유니크
- server_id + event_time 조합으로 중복 이벤트 방지 (5분 내)
- execution_result는 최대 10MB 제한
```

## 3단계: CRUD 기능 요구사항
```
**Create (생성)**:
- [x] SaltStack 명령어 등록 (유효성 검증)
- [x] CloudWatch 이벤트 수신 및 저장
- [x] Runbook 스크립트 등록
- [x] 실행 로그 자동 생성
- [x] 중복 이벤트 체크 (5분 내 동일 서버/이벤트)

**Read (조회)**:
- [x] 이벤트 목록 조회 (페이징, 날짜/심각도 필터링)
- [x] 실시간 대시보드 (Fatal/Critical 통계)
- [x] 명령어 실행 히스토리 조회
- [x] Runbook 검색 (서비스별, 키워드별)
- [x] 서버별 이벤트 현황 조회

**Update (수정)**:
- [x] 이벤트 상태 업데이트 (처리중 → 완료)
- [x] SaltStack 명령어 스크립트 수정
- [x] Runbook 내용 업데이트
- [x] 실행 결과 업데이트

**Delete (삭제)**:
- [x] 소프트 삭제 (이벤트 로그는 보관)
- [x] 오래된 로그 아카이빙 (90일 이상)
- [x] 권한 검증 (관리자만 삭제 가능)
```

## 4단계: 필수 구현 기능
```
**보안**:
- [x] JWT 기반 인증
- [x] 역할 기반 권한 관리 (Admin, Operator, Viewer)
- [x] SaltStack 명령어 실행 권한 검증
- [x] SQL 인젝션 방지
- [x] CORS 설정 (Slack Webhook 허용)

**성능**:
- [x] 이벤트 테이블 인덱싱 (server_id, event_time, severity)
- [x] 페이징 처리 (기본 50개씩)
- [x] 비동기 SaltStack 명령어 실행

**에러 처리**:
- [x] 글로벌 에러 핸들러
- [x] SaltStack 실행 실패 시 재시도 로직
- [x] Slack 알림 실패 시 대체 알림
- [x] 타임아웃 처리 (명령어 실행 5분 제한)

**로깅 및 모니터링**:
- [x] 모든 API 호출 로깅
- [x] SaltStack 명령어 실행 로그
- [x] 성능 메트릭 (응답시간, 처리량)
- [x] 헬스 체크 엔드포인트 (/health)
```

## 5단계: AWS 인프라 요구사항
```
**컴퓨팅**:
- [x] EC2 선택 (Docker 컨테이너로 Flask 앱 배포)
- [x] Auto Scaling Group (최소 2대, 최대 5대)
- [x] Application Load Balancer

**데이터베이스**:
- [x] RDS MySQL 선택 (Multi-AZ 구성)
- [x] 자동 백업 (7일 보관)
- [x] 읽기 전용 복제본 (대시보드 조회용)

**네트워킹**:
- [x] VPC 구성 (10.0.0.0/16)
- [x] Public 서브넷 (ALB용), Private 서브넷 (EC2, RDS용)
- [x] Security Group (Flask:5000, MySQL:3306, SSH:22)
- [x] NAT Gateway (Private 서브넷 인터넷 접근용)

**보안**:
- [x] IAM 역할 (EC2 → CloudWatch, SNS, Bedrock 접근)
- [x] Secrets Manager (DB 자격증명, Slack Token)
- [x] WAF 설정 (SQL 인젝션, XSS 방어)
- [x] SSL/TLS 인증서 (ACM)

**모니터링**:
- [x] CloudWatch 로그 및 메트릭
- [x] CloudWatch Agent (EC2 프로세스 모니터링)
- [x] EventBridge (CloudWatch → Lambda 연동)
- [x] 알람 설정 (MySQL 프로세스 다운, CPU, 메모리 사용률)

**Bedrock**:
- [x] Claude-3 Sonnet 모델 사용
- [x] Lambda를 통한 Bedrock API 호출
- [x] Runbook에 해당 내용이 없을 때 AI 조치 방법 생성
- [x] 토큰 사용량 모니터링 및 비용 최적화
- [x] Bedrock 호출 로깅 및 응답 시간 추적
- [x] IAM 정책으로 Bedrock 접근 제어
- [x] 모델 응답 캐싱으로 중복 호출 방지

**SNS 통합**:
- [x] SNS 토픽 생성 (Slack 알림용)
- [x] Lambda 함수로 SNS → Slack Webhook 연동
- [x] 이벤트 심각도별 알림 채널 분리
```

## 6단계: IaC 구현 요청
```
다음 도구로 인프라를 코드화해주세요:
- [x] Terraform

**포함할 리소스**:
- VPC 및 네트워킹 (서브넷, 라우팅 테이블, IGW, NAT)
- EC2 인스턴스 및 Auto Scaling
- RDS MySQL 클러스터
- Application Load Balancer
- Security Group 및 IAM 역할
- CloudWatch 및 EventBridge 설정
- Lambda 함수 (SNS → Slack, Bedrock 호출)
- SNS 토픽 및 구독
- Secrets Manager

**환경별 구성**:
- [x] 개발 환경 (단일 AZ, t3.micro)
- [x] 프로덕션 환경 (Multi-AZ, t3.medium)
```

## 7단계: 배포 및 CI/CD
```
**배포 전략**:
- [x] Rolling 배포 (Docker 컨테이너 순차 교체)

**CI/CD 파이프라인**:
- [x] GitHub Actions 기반
- [x] 코드 빌드 및 테스트 (pytest, flake8)
- [x] Docker 이미지 빌드 및 ECR 푸시
- [x] Terraform 인프라 배포
- [x] EC2 인스턴스에 Docker 컨테이너 배포
- [x] 배포 후 헬스 체크 및 Slack 알림
```

## 핵심 워크플로우
```
1. CloudWatch Agent → MySQL 프로세스 다운 감지
2. EventBridge → Lambda 트리거
3. Lambda → SNS → Slack 알림 + Flask API 호출
4. Flask → Runbook 검색 → 없으면 Bedrock Claude 호출
5. SaltStack 명령어 실행 → 결과 저장
6. 처리 완료 시 Slack 최종 알림
7. (선택) Slack 요청으로 통계 카드 표시
```

---

## 사용 예시
위 양식을 채워서 다음과 같이 요청하세요:

"1단계부터 7단계까지 순서대로 구현해주세요. 각 단계가 완료되면 다음 단계로 진행하겠습니다."