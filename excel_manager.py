import pandas as pd
import os
from datetime import datetime

class ExcelManager:
    def __init__(self, output_dir="reports"):
        self.output_dir = output_dir
        # reports 폴더가 없으면 생성
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        self.today = datetime.today().strftime("%Y%m%d")
        
    def export_to_excel(self, df_analyzed):
        """분석된 데이터프레임을 예쁜 엑셀 파일로 저장합니다."""
        if df_analyzed is None or df_analyzed.empty:
            print("엑셀로 저장할 분석 데이터가 없습니다.")
            return None
            
        filename = f"stock_analysis_report_{self.today}.xlsx"
        filepath = os.path.join(self.output_dir, filename)
        
        try:
            # openpyxl 엔진을 사용하여 엑셀 파일로 내보내기
            with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                df_analyzed.to_excel(writer, sheet_name='추천종목_스코어링', index=False)
            
            print(f"✅ 엑셀 리포트가 성공적으로 저장되었습니다: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"❌ 엑셀 저장 중 오류 발생: {e}")
            return None

if __name__ == "__main__":
    # 테스트용
    manager = ExcelManager()
    # manager.export_to_excel(테스트데이터프레임)
