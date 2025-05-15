#截屏股票代码或价格并上传到redis上
#建议白字黑底文字,识别小数方面还有问题,需要个性化优化
#需要安装 tesseract-ocr-w64-setup-v5.3.0.20221214
#v20250516

import re
import time
import redis
import sys
import threading
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QPushButton, QVBoxLayout,
                             QHBoxLayout, QWidget, QLabel, QFrame, QLineEdit,
                             QGroupBox, QFormLayout, QMessageBox, QCheckBox)
from PyQt5.QtCore import Qt, QRect, QPoint, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QPen, QPainter, QScreen
from PIL import ImageGrab, Image
import pytesseract

# 配置 Tesseract 路径
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Redis 配置
redis_host1 = '127.0.0.1'
redis_pool = redis.ConnectionPool(host=redis_host1, port=6379, db=0)
redis_client = redis.Redis(connection_pool=redis_pool)

# 获取当前文件名作为Redis key的前缀
def get_file_prefix():
    # 获取当前脚本的文件名
    current_file = os.path.basename(__file__)
    # 移除扩展名
    file_name = os.path.splitext(current_file)[0]
    
    # 如果文件名为空，使用默认值
    if not file_name:
        return "_duo_top15_list_146"
    
    # 返回文件名
    return file_name

# Redis 列表存储键 - 使用文件名作为键
output_file_list = get_file_prefix()
print(f"当前使用的Redis key: {output_file_list}")

# 全局变量存储选择的屏幕区域
selected_region = None
ocr_thread = None
is_running = False

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

# 屏幕选择器类
class ScreenSelector(QWidget):
    regionSelected = pyqtSignal(tuple)  # 发送选择的区域信号
    
    def __init__(self):
        super().__init__()
        # 设置无边框、置顶窗口
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        
        # 确保窗口覆盖整个屏幕
        self.showFullScreen()
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(0, 0, screen.width(), screen.height())
        
        # 设置半透明背景
        self.setStyleSheet("background-color: rgba(0, 0, 0, 100);")
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        self.begin = QPoint()
        self.end = QPoint()
        self.setMouseTracking(True)
        
        # 显示操作提示
        self.hint_label = QLabel("点击并拖动鼠标选择截图区域，松开鼠标完成选择", self)
        self.hint_label.setStyleSheet("color: white; background-color: rgba(0, 0, 0, 150); padding: 10px;")
        self.hint_label.move(50, 50)
        self.hint_label.adjustSize()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor(255, 0, 0), 3))
        painter.setBrush(QColor(255, 0, 0, 30))
        
        # 绘制全屏半透明遮罩
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        
        # 绘制选择区域
        if not self.begin.isNull() and not self.end.isNull():
            rect = QRect(self.begin, self.end)
            painter.drawRect(rect)
            
            # 在选择框旁边显示尺寸信息
            width = abs(self.begin.x() - self.end.x())
            height = abs(self.begin.y() - self.end.y())
            info_text = f"区域: {width}x{height} - 起点: ({self.begin.x()}, {self.begin.y()})"
            
            # 使用painter绘制信息，避免使用QLabel可能导致的闪烁
            painter.setPen(Qt.white)
            painter.drawText(self.end.x() + 10, self.end.y() + 20, info_text)
    
    def mousePressEvent(self, event):
        self.begin = event.pos()
        self.end = event.pos()
        self.update()
    
    def mouseMoveEvent(self, event):
        self.end = event.pos()
        self.update()
    
    def mouseReleaseEvent(self, event):
        self.end = event.pos()
        
        # 确保坐标按左上、右下排序
        x1 = min(self.begin.x(), self.end.x())
        y1 = min(self.begin.y(), self.end.y())
        x2 = max(self.begin.x(), self.end.x())
        y2 = max(self.begin.y(), self.end.y())
        
        # 发出信号
        self.regionSelected.emit((x1, y1, x2, y2))
        self.close()

def preprocess_image(image):
    """
    增强预处理以更好地识别彩色文字
    """
    # 方法1: 调整每个颜色通道的对比度，增强红色和绿色文字
    r, g, b = image.split()
    
    # 增强红色通道
    r = r.point(lambda i: min(255, i * 1.5))
    
    # 增强绿色通道
    g = g.point(lambda i: min(255, i * 1.5))
    
    # 重新合并通道
    enhanced_image = Image.merge('RGB', (r, g, b))
    
    # 转为灰度
    gray_image = enhanced_image.convert('L')
    
    # 自适应阈值处理 (模拟)
    # 这里使用简单的方法，实际项目中可以考虑更复杂的自适应阈值算法
    width, height = gray_image.size
    total = 0
    for x in range(0, width, 10):  # 采样以加快速度
        for y in range(0, height, 10):
            total += gray_image.getpixel((x, y))
    
    # 计算平均值并基于此设置阈值
    count = (width//10) * (height//10)
    if count > 0:
        avg = total / count
        threshold = max(80, min(180, avg - 30))  # 动态阈值，但保持在合理范围内
    else:
        threshold = 120  # 默认值
    
    print(f"使用的二值化阈值: {threshold}")
    binary_image = gray_image.point(lambda x: 0 if x < threshold else 255, '1')
    
    return binary_image

def get_numbers_from_coordinates(x1, y1, x2, y2):
    """
    根据输入坐标截取屏幕并识别多组数字（包括负数和小数）
    """
    try:
        # 截取屏幕
        screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
        
        # 可选：保存原始截图用于调试
        # screenshot.save("original_capture.png")
        
        # 预处理截图
        processed_image = preprocess_image(screenshot)
        
        # 可选：保存处理后图像用于调试
        # processed_image.save("processed_capture.png")
        
        # 使用 Tesseract OCR 识别文本 - 特殊配置以提高小数点识别率
        text = pytesseract.image_to_string(
            processed_image,
            config='--psm 6 --oem 3 -c tessedit_char_whitelist=0123456789.- -c preserve_interword_spaces=0'
        )
        
        print(f"原始OCR文本: '{text.strip()}'")
        
        # 增强的小数点处理逻辑
        # 首先尝试标准匹配
        matches = re.findall(r'-?\d+\.\d+|-?\d+', text)
        
        # 获取是否启用小数点优化的状态 (需要从MainWindow实例中获取)
        import inspect
        frame = inspect.currentframe()
        while frame:
            if 'self' in frame.f_locals and hasattr(frame.f_locals['self'], 'decimal_checkbox'):
                decimal_optimization_enabled = frame.f_locals['self'].decimal_checkbox.isChecked()
                break
            frame = frame.f_back
        else:
            # 如果无法获取，默认启用
            decimal_optimization_enabled = True
        
        # 如果启用了小数点优化
        if decimal_optimization_enabled:
            # 优化处理：3-5位整数自动除以100
            enhanced_matches = []
            for match in matches:
                # 如果是整数，检查位数
                if '.' not in match:
                    digits = match
                    is_negative = False
                    
                    # 处理负号
                    if digits.startswith('-'):
                        is_negative = True
                        digits = digits[1:]
                    
                    # 只处理3-5位的整数
                    if 3 <= len(digits) <= 5:
                        # 将整数除以100（向左移动小数点两位）
                        value = float(match) / 100
                        # 转换为字符串，保留两位小数
                        decimal_value = f"{value:.2f}"
                        enhanced_matches.append(decimal_value)
                    else:
                        enhanced_matches.append(match)
                else:
                    # 已经是小数的情况，保持不变
                    enhanced_matches.append(match)
            
            # 使用优化后的结果
            if enhanced_matches:
                matches = enhanced_matches
        
        print(f"增强匹配的数字: {matches}")
        
        # 如果没有匹配到有效数字，用默认值替代
        if not matches:
            matches = ['880559']
        # 如果结果少于 10 个，填充到 10 个
        while len(matches) < 10:
            matches.append('880559')
        return matches[:10]
    except Exception as e:
        print(f"Error during screen capture or OCR: {e}")
        return ['880559'] * 10  # 返回 10 个默认值

# OCR识别线程
def ocr_thread_function(region):
    global is_running, output_file_list
    while is_running:
        try:
            x1, y1, x2, y2 = region
            print(f"正在处理区域: 坐标 ({x1}, {y1}, {x2}, {y2})")

            # 获取识别结果
            results = get_numbers_from_coordinates(x1, y1, x2, y2)
            print(f"识别结果: {results}")

            # 更新到 Redis
            safe_update_list(output_file_list, results)
        except Exception as e:
            print(f"程序运行时出错: {e}")
        
        # 每 5 秒循环一次
        time.sleep(5)

# 主窗口类
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()
        
    def initUI(self):
        self.setWindowTitle('屏幕数字识别工具 - 高级版')
        self.setGeometry(100, 100, 550, 680)  # 再增加一点窗口高度
        
        # 创建中央组件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 创建布局
        main_layout = QVBoxLayout(central_widget)
        
        # Redis Key显示与编辑区域
        key_layout = QHBoxLayout()
        key_label = QLabel("Redis Key:")
        key_layout.addWidget(key_label)
        
        self.key_edit = QLineEdit(output_file_list)
        self.key_edit.setToolTip("编辑此处可以修改Redis存储的key")
        self.key_edit.textChanged.connect(self.update_redis_key)
        key_layout.addWidget(self.key_edit)
        
        main_layout.addLayout(key_layout)
        
        # 添加手动输入坐标区域
        coords_group = QGroupBox("手动输入坐标 (提高识别率)")
        coords_layout = QFormLayout()
        
        # 创建x1, y1, x2, y2输入框
        self.x1_edit = QLineEdit("293")
        self.y1_edit = QLineEdit("598")
        self.x2_edit = QLineEdit("354")
        self.y2_edit = QLineEdit("746")
        
        # 添加到表单布局
        coords_layout.addRow("左上X:", self.x1_edit)
        coords_layout.addRow("左上Y:", self.y1_edit)
        coords_layout.addRow("右下X:", self.x2_edit)
        coords_layout.addRow("右下Y:", self.y2_edit)
        
        # 添加使用手动坐标按钮
        manual_coords_btn = QPushButton("使用手动坐标")
        manual_coords_btn.clicked.connect(self.use_manual_coords)
        coords_layout.addRow("", manual_coords_btn)
        
        # 设置组布局
        coords_group.setLayout(coords_layout)
        main_layout.addWidget(coords_group)
        
        # 添加小数点优化选项
        decimal_group = QGroupBox("小数点优化设置")
        decimal_layout = QVBoxLayout()
        
        # 创建复选框，默认选中
        self.decimal_checkbox = QCheckBox("启用小数点优化 (3-5位数自动除以100，如252→2.52)")
        self.decimal_checkbox.setChecked(True)  # 默认选中
        self.decimal_checkbox.setStyleSheet("font-size: 14px;")
        decimal_layout.addWidget(self.decimal_checkbox)
        
        # 添加说明标签
        help_label = QLabel("此选项有助于正确识别可能缺少小数点的数值，特别适用于金额数字")
        help_label.setStyleSheet("color: #666; font-style: italic;")
        help_label.setWordWrap(True)
        decimal_layout.addWidget(help_label)
        
        decimal_group.setLayout(decimal_layout)
        main_layout.addWidget(decimal_group)
        
        # 状态显示区域
        self.status_label = QLabel("未选择区域")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 14px; margin: 10px;")
        main_layout.addWidget(self.status_label)
        
        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(line)
        
        # 选区按钮
        select_btn = QPushButton("选择屏幕区域")
        select_btn.setStyleSheet("font-size: 16px; padding: 10px;")
        select_btn.clicked.connect(self.select_region)
        main_layout.addWidget(select_btn)
        
        # 开始/停止按钮
        self.start_stop_btn = QPushButton("开始识别")
        self.start_stop_btn.setStyleSheet("font-size: 16px; padding: 10px;")
        self.start_stop_btn.setEnabled(False)  # 开始时禁用
        self.start_stop_btn.clicked.connect(self.toggle_recognition)
        main_layout.addWidget(self.start_stop_btn)
        
        # 显示结果区域 - 增大区域以显示10个结果
        self.result_label = QLabel("识别结果将在这里显示")
        self.result_label.setAlignment(Qt.AlignLeft)
        self.result_label.setStyleSheet("font-size: 14px; margin: 10px; min-height: 250px; background-color: #f0f0f0; padding: 10px;")
        main_layout.addWidget(self.result_label)
        
        # 设置定时器更新结果显示
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_results)
        self.timer.start(1000)  # 每秒更新一次
    
    def update_redis_key(self, text):
        global output_file_list
        if text.strip():  # 确保key不为空
            output_file_list = text
            print(f"Redis key已更新为: {output_file_list}")
    
    def use_manual_coords(self):
        """使用手动输入的坐标"""
        try:
            x1 = int(self.x1_edit.text())
            y1 = int(self.y1_edit.text())
            x2 = int(self.x2_edit.text())
            y2 = int(self.y2_edit.text())
            
            # 验证坐标有效性
            if x1 >= x2 or y1 >= y2:
                QMessageBox.warning(self, "坐标错误", "坐标无效：确保 x1 < x2 且 y1 < y2")
                return
            
            # 设置选定区域
            global selected_region
            selected_region = (x1, y1, x2, y2)
            
            # 更新状态显示
            self.status_label.setText(f"已选择区域: ({x1}, {y1}) 到 ({x2}, {y2}) - 大小: {x2-x1}x{y2-y1}")
            
            # 启用开始按钮
            self.start_stop_btn.setEnabled(True)
            
            # 显示成功消息
            QMessageBox.information(self, "成功", "已设置手动坐标！这种方式通常可以提供更高的识别率。")
            
        except ValueError:
            QMessageBox.warning(self, "输入错误", "请确保所有坐标都是有效的整数")
        
    def select_region(self):
        # 创建并显示屏幕选择器
        self.selector = ScreenSelector()
        self.selector.regionSelected.connect(self.on_region_selected)
        self.selector.show()
        
    def on_region_selected(self, region):
        global selected_region
        selected_region = region
        x1, y1, x2, y2 = region
        
        # 更新状态显示
        self.status_label.setText(f"已选择区域: ({x1}, {y1}) 到 ({x2}, {y2}) - 大小: {x2-x1}x{y2-y1}")
        
        # 更新坐标输入框
        self.x1_edit.setText(str(x1))
        self.y1_edit.setText(str(y1))
        self.x2_edit.setText(str(x2))
        self.y2_edit.setText(str(y2))
        
        # 启用开始按钮
        self.start_stop_btn.setEnabled(True)
        
    def toggle_recognition(self):
        global is_running, ocr_thread
        
        if not is_running:
            # 开始识别
            is_running = True
            self.start_stop_btn.setText("停止识别")
            
            # 启动OCR线程
            ocr_thread = threading.Thread(target=ocr_thread_function, args=(selected_region,))
            ocr_thread.daemon = True
            ocr_thread.start()
        else:
            # 停止识别
            is_running = False
            self.start_stop_btn.setText("开始识别")
            
            # 等待线程结束
            if ocr_thread:
                ocr_thread.join(0.1)  # 短暂等待
                
    def update_results(self):
        """更新结果显示"""
        if is_running:
            try:
                # 从Redis获取最新结果
                results = redis_client.lrange(output_file_list, 0, -1)
                if results:
                    # 转换字节到字符串
                    result_strings = [r.decode('utf-8') for r in results]
                    # 显示全部10个结果
                    result_text = f"Redis Key: {output_file_list}\n最新识别结果:\n" + "\n".join(result_strings[:10])
                    self.result_label.setText(result_text)
            except Exception as e:
                self.result_label.setText(f"获取结果时出错: {e}")
    
    def closeEvent(self, event):
        """窗口关闭时停止所有活动"""
        global is_running
        is_running = False
        if ocr_thread:
            ocr_thread.join(1.0)
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main() 
