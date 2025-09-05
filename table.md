**엔티티명 1**: SaltStackCommand
**설명: event를 받아서 saltstack이 수행해야하는 커맨드를 가지고 있는 테이블**
**필수 필드**:
- id: INT (Primary Key) - 명령어 고유 ID
- before_command_script: TEXT - 이벤트 발생시 서버의 상태를 체크할 스크립트
- act_command_script: TEXT - 이벤트를 조치 할 스크립트
- check_command_script: TEXT - 이벤트 조치 후 상태를 체크할 스크립트
- auto_act : Boolean - auto로 실행할 건지에 대한 여부
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
- auto_act: Bollean - auto로 접속했는지 아닌지?
- execution_result: TEXT - 실행 결과
- execution_status: ENUM('success', 'failed', 'timeout') - 실행 상태
- executed_at: TIMESTAMP - 실행 시간
- response_time: INT - 응답 시간 (초)

