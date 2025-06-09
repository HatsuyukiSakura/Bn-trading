import datetime

def convert_ms_to_datetime(timestamp_ms):
    """將毫秒時間戳轉換為 datetime 物件 (UTC)。"""
    if timestamp_ms is None:
        return None
    return datetime.datetime.fromtimestamp(timestamp_ms / 1000, tz=datetime.timezone.utc)

def format_datetime_to_string(dt_object):
    """將 datetime 物件格式化為可讀的字串。"""
    if dt_object is None:
        return ""
    return dt_object.strftime('%Y-%m-%d %H:%M:%S UTC')

def is_valid_json(data_string):
    """檢查字串是否為有效的 JSON 格式。"""
    try:
        json.loads(data_string)
        return True
    except json.JSONDecodeError:
        return False

# 您可以在這裡添加更多的通用工具函式
# 例如：
# def calculate_ema(data, window):
#     # 計算指數移動平均線
#     pass

# def encrypt_data(data, key):
#     # 加密數據
#     pass

