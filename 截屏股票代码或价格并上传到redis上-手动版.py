#对比截屏股票代码或价格并上传到redis上.py 这个版本,小数识别率更高,不清楚为什么

import re
import time
import redis
from PIL import ImageGrab, Image
import pytesseract

# 配置 Tesseract 路径
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Redis 配置
redis_host1 = '127.0.0.1'
redis_pool = redis.ConnectionPool(host=redis_host1, port=6379, db=0)
redis_client = redis.Redis(connection_pool=redis_pool)

# Redis 列表存储键
output_file_list = "_duo_top15_list_146_GN_ZS_F"

def safe_update_list(key, value_list):
    """
    安全地更新 Redis 中的列表，确保客户端读取时不会遇到空值。
    
    :param key: Redis 中的目标键
    :param value_list: 要写入的列表
    """
    try:
        # 创建一个临时键名
        temp_key = f"{key}_temp"
        
        # 使用 pipeline 确保事务性
        pipeline = redis_client.pipeline()
        
        # 将新列表写入到临时键
        pipeline.delete(temp_key)  # 确保临时键为空
        pipeline.rpush(temp_key, *value_list)
        
        # 用 RENAME 原子操作替换目标键
        pipeline.rename(temp_key, key)
        
        # 执行事务
        pipeline.execute()
        print(f"Successfully updated key '{key}' with new list.")
    
    except Exception as e:
        print(f"Error updating key '{key}': {e}")

# 指定一组分辨率 (x1, y1, x2, y2)
resolution = (300, 102,376, 399)  # 替换为你的实际分辨率坐标
#55,598,135,716 #293, 598,354, 746
def preprocess_image(image):
    """
    对截图进行预处理：灰度化和二值化
    """
    gray_image = image.convert('L')
    binary_image = gray_image.point(lambda x: 0 if x < 120 else 255, '1')
    return binary_image

def get_numbers_from_coordinates(x1, y1, x2, y2):
    """
    根据输入坐标截取屏幕并识别多组数字（包括负数和小数）
    """
    try:
        # 截取屏幕
        screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
        # 预处理截图
        processed_image = preprocess_image(screenshot)
        # 使用 Tesseract OCR 识别文本，允许负号和小数点
        text = pytesseract.image_to_string(
            processed_image,
            config='--psm 11 --oem 3 -c tessedit_char_whitelist=0123456789.-'
        )
        # 提取所有的数字（包括负数和小数）
        matches = re.findall(r'-?\d+\.?\d*', text)
        # 如果没有匹配到有效数字，用默认值替代
        if not matches:
            matches = ['0']
        # 如果结果少于 10 个，填充到 10 个
        while len(matches) < 10:
            matches.append('0')
        return matches[:10]
    except Exception as e:
        print(f"Error during screen capture or OCR: {e}")
        return ['0'] * 10  # 返回 10 个默认值

def main():
    while True:
        try:
            # 直接处理预定义的分辨率
            x1, y1, x2, y2 = resolution
            print(f"正在处理分辨率: 坐标 ({x1}, {y1}, {x2}, {y2})")

            # 获取识别结果
            results = get_numbers_from_coordinates(x1, y1, x2, y2)
            print(f"识别结果: {results}")

            # 更新到 Redis
            safe_update_list(output_file_list, results)

        except Exception as e:
            print(f"程序运行时出错: {e}")
        
        # 每 5 秒循环一次
        time.sleep(5)

if __name__ == "__main__":
    main()
