import time
import requests
import threading
from datetime import datetime
import pymysql
import os
from dotenv import load_dotenv
load_dotenv()
class BatchScheduler:
    def __init__(self, base_url="http://localhost:3000"):
        self.base_url = base_url
        self.running = False
        self.thread = None
    def start(self):
        """스케줄러 시작"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
            self.thread.start()
            print("Batch scheduler started - running every 1 minute")
    def stop(self):
        """스케줄러 중지"""
        self.running = False
        if self.thread:
            self.thread.join()
        print("Batch scheduler stopped")
    def _run_scheduler(self):
        """1분마다 배치 처리 실행"""
        while self.running:
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # 1. 배치 상태 조회
                status_response = requests.get(f"{self.base_url}/batch/status", timeout=30)
                if status_response.status_code != 200:
                    print(f"[{timestamp}] Failed to get batch status: HTTP {status_response.status_code}")
                    time.sleep(60)
                    continue
                batch_status = status_response.json()['batch_status']
                last_processed_id = batch_status['last_processed_id']
                # 2. 새로운 레코드 확인
                try:
                    # DB 직접 연결로 새 레코드 조회
                    connection = pymysql.connect(
                        host=os.getenv('DB_HOST', 'localhost'),
                        port=int(os.getenv('DB_PORT', '3306')),
                        user=os.getenv('DB_USER', 'admin'),
                        password=os.getenv('DB_PASSWORD', 'tt'),
                        database=os.getenv('DB_NAME', 'rescuebot'),
                        charset='utf8mb4'
                    )
                    with connection.cursor() as cursor:
                        cursor.execute("""
                            SELECT id FROM cloudwatch_alarm_metrics
                            WHERE id > %s
                            ORDER BY id ASC
                        """, (last_processed_id,))
                        new_record_ids = [row[0] for row in cursor.fetchall()]
                    connection.close()
                    if not new_record_ids:
                        print(f"[{timestamp}] No new records to process")
                    else:
                        print(f"[{timestamp}] Found {len(new_record_ids)} new records: {new_record_ids}")
                        # 3. 각 레코드를 개별 처리
                        processed_count = 0
                        for record_id in new_record_ids:
                            try:
                                single_response = requests.post(
                                    f"{self.base_url}/batch/process-single",
                                    json={"id": record_id},
                                    headers={'Content-Type': 'application/json'},
                                    timeout=30
                                )
                                if single_response.status_code == 200:
                                    result = single_response.json()
                                    print(f"[{timestamp}] Processed record {record_id}: {result['metric_id']}")
                                    print(f"[{timestamp}] Salt Command: {result['salt_command']}")
                                    print(f"[{timestamp}] AI Response: {result['ai_response']}")
                                    print(f"[{timestamp}] ---")
                                    processed_count += 1
                                else:
                                    print(f"[{timestamp}] Failed to process record {record_id}: HTTP {single_response.status_code}")
                                    print(f"[{timestamp}] Error response: {single_response.text}")
                            except Exception as e:
                                print(f"[{timestamp}] Error processing record {record_id}: {str(e)}")
                                import traceback
                                print(f"[{timestamp}] Traceback: {traceback.format_exc()}")
                        # 4. 배치 상태 업데이트
                        if processed_count > 0:
                            last_id = max(new_record_ids[:processed_count])
                            # 배치 상태 업데이트는 process-single에서 자동으로 처리됨
                            print(f"[{timestamp}] Successfully processed {processed_count}/{len(new_record_ids)} records")
                            print(f"[{timestamp}] Last processed ID updated to: {last_id}")
                        else:
                            print(f"[{timestamp}] No records were successfully processed")
                except Exception as db_error:
                    print(f"[{timestamp}] Database connection error: {str(db_error)}")
                    import traceback
                    print(f"[{timestamp}] DB Error traceback: {traceback.format_exc()}")
            except Exception as e:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] Batch scheduler error: {str(e)}")
                import traceback
                print(f"[{timestamp}] Scheduler error traceback: {traceback.format_exc()}")
            # 1분 대기
            time.sleep(60)


batch_scheduler = BatchScheduler()
