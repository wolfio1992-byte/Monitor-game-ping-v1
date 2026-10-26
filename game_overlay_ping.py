import datetime
import os
import re
import subprocess
import sys
import time
import psutil
from PyQt6.QtCore import QPoint, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

CONFIG_FILENAME = 'config.txt'
LOG_FILENAME = 'ping_history.txt'


def save_target_process(process_name):
  try:
    with open(CONFIG_FILENAME, 'w', encoding='utf-8') as f:
      f.write(process_name.strip())
  except:
    pass


def load_target_process():
  if os.path.exists(CONFIG_FILENAME):
    try:
      with open(CONFIG_FILENAME, 'r', encoding='utf-8') as f:
        content = f.read().strip()
        if content:
          return content
    except:
      pass
  return 'stalker2.exe'


class GameSelectDialog(QWidget):

  def __init__(self, on_selected_callback):
    super().__init__()
    self.on_selected_callback = on_selected_callback
    self.initUI()

  def initUI(self):
    self.setWindowTitle('Вибір гри для моніторингу пінгу')
    self.resize(360, 200)
    self.setWindowFlags(
        Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Dialog
    )

    layout = QVBoxLayout()
    title_label = QLabel('Оберіть або введіть процес гри (.exe):', self)
    title_label.setFont(QFont('Arial', 10, QFont.Weight.Bold))
    layout.addWidget(title_label)

    self.combo = QComboBox(self)
    self.combo.setEditable(True)
    self.combo.setFont(QFont('Arial', 10))

    saved_proc = load_target_process()
    self.combo.addItem(saved_proc)
    defaults = ['stalker2.exe', 'cs2.exe', 'dota2.exe', 'r5apex.exe']
    for d in defaults:
      if d != saved_proc:
        self.combo.addItem(d)

    layout.addWidget(self.combo)

    btn_scan = QPushButton('🔍 Знайти запущені з мережею', self)
    btn_scan.setStyleSheet('padding: 6px; font-size: 11px;')
    btn_scan.clicked.connect(self.scan_active_processes)
    layout.addWidget(btn_scan)

    btn_start = QPushButton('🚀 Запустити оверлей пінгу', self)
    btn_start.setStyleSheet(
        'background-color: #2ecc71; color: white; font-weight: bold; padding:'
        ' 10px; font-size: 12px; border-radius: 4px;'
    )
    btn_start.clicked.connect(self.start_overlay)
    layout.addWidget(btn_start)

    self.setLayout(layout)

  def scan_active_processes(self):
    self.combo.clear()
    found = set()
    ignore_list = {
        'system',
        'svchost.exe',
        'chrome.exe',
        'discord.exe',
        'steam.exe',
        'explorer.exe',
        'epicgameslauncher.exe',
        'battlenet.exe',
        'telegram.exe',
        'onedrive.exe',
        'searchapp.exe',
    }

    for proc in psutil.process_iter(['name', 'pid']):
      try:
        name = proc.info['name']
        if name and name.lower() not in ignore_list:
          if proc.connections(kind='inet'):
            found.add(name)
      except:
        pass

    if found:
      for name in sorted(found):
        self.combo.addItem(name)
    else:
      self.combo.addItem('Активних мережевих процесів не знайдено')

  def start_overlay(self):
    selected_text = self.combo.currentText().strip()
    if (
        selected_text
        and selected_text != 'Активних мережевих процесів не знайдено'
    ):
      save_target_process(selected_text)
      self.close()
      self.on_selected_callback()


class PingWorker(QThread):
  ping_updated = pyqtSignal(str, str)

  def log_message(self, message):
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
      with open(LOG_FILENAME, 'a', encoding='utf-8') as f:
        f.write(f'[{timestamp}] {message}\n')
    except Exception as e:
      print(f'Помилка запису в лог: {e}')

  def run(self):
    target_process = load_target_process()
    self.log_message(
        f'=== Сесію розпочато. Цільовий процес: {target_process} ==='
    )

    while True:
      target_process = load_target_process()
      proc = self.find_process(target_process)

      if not proc:
        self.ping_updated.emit(f'Очікування ({target_process})', 'gray')
        time.sleep(3)
        continue

      self.ping_updated.emit('Пошук сервера...', 'yellow')
      self.log_message(
          f'Знайдено процес {target_process} (PID: {proc.pid}). Пошук'
          " з'єднань..."
      )

      while proc.is_running():
        endpoints = self.get_remote_endpoints(proc)
        if not endpoints:
          self.ping_updated.emit("Немає активних з'єднань", 'orange')
          time.sleep(2)
          continue

        ip, port = list(endpoints)[0]
        latency = self.measure_latency_system(ip)

        if latency is not None:
          if latency < 50:
            color = '#2ecc71'
          elif latency < 100:
            color = '#f1c40f'
          else:
            color = '#e74c3c'

          text = f'PING: {latency} ms'
          self.ping_updated.emit(text, color)
          self.log_message(f'Сервер: {ip}:{port} | Пінгування: {latency} ms')
        else:
          self.ping_updated.emit(f'Таймаут ({ip})', 'gray')
          self.log_message(
              f'Сервер: {ip}:{port} | Таймаут (немає відповіді на ping)'
          )

        time.sleep(1.5)

  def find_process(self, name):
    for proc in psutil.process_iter(['name', 'pid']):
      try:
        if proc.info['name'] and name.lower() in proc.info['name'].lower():
          return proc
      except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return None

  def get_remote_endpoints(self, proc):
    endpoints = set()
    try:
      for conn in proc.connections(kind='inet'):
        if conn.raddr:
          ip, port = conn.raddr
          if not ip.startswith(('127.', '192.168.', '10.', '172.')):
            endpoints.add((ip, port))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
      pass
    return endpoints

  def measure_latency_system(self, ip):
    try:
      output = subprocess.check_output(
          ['ping', '-n', '1', '-w', '1000', ip],
          stderr=subprocess.STDOUT,
          text=True,
          encoding='cp866',
      )
      match = re.search(
          r'(?:time|час|время)[=<](\d+)\s*(?:ms|мс)', output, re.IGNORECASE
      )
      if match:
        return int(match.group(1))
    except Exception:
      pass
    return None


class PingOverlay(QWidget):

  def __init__(self):
    super().__init__()
    self.initUI()
    self.old_pos = QPoint()

  def initUI(self):
    self.setWindowFlags(
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
    )
    self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    layout = QVBoxLayout()
    layout.setContentsMargins(5, 5, 5, 5)

    self.label = QLabel('Ініціалізація...', self)
    self.label.setFont(QFont('Arial', 12, QFont.Weight.Bold))
    self.label.setStyleSheet("""
            background-color: rgba(0, 0, 0, 160);
            color: white;
            padding: 6px 12px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 40);
        """)
    self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    layout.addWidget(self.label)
    self.setLayout(layout)

    self.setGeometry(100, 100, 140, 45)

    self.worker = PingWorker()
    self.worker.ping_updated.connect(self.update_ping_display)
    self.worker.start()

  def update_ping_display(self, text, color):
    self.label.setText(text)
    self.label.setStyleSheet(f"""
            background-color: rgba(0, 0, 0, 160);
            color: {color};
            padding: 6px 12px;
            border-radius: 6px;
            border: 1px solid {color};
        """)

  def mousePressEvent(self, event: QMouseEvent):
    if event.button() == Qt.MouseButton.LeftButton:
      self.old_pos = event.globalPosition().toPoint()

  def mouseMoveEvent(self, event: QMouseEvent):
    if event.buttons() == Qt.MouseButton.LeftButton:
      delta = event.globalPosition().toPoint() - self.old_pos
      self.move(self.pos() + delta)
      self.old_pos = event.globalPosition().toPoint()


if __name__ == '__main__':
  app = QApplication(sys.argv)
  overlay_window = None


  def launch_overlay():
    global overlay_window
    overlay_window = PingOverlay()
    overlay_window.show()


  selector = GameSelectDialog(launch_overlay)
  selector.show()
  sys.exit(app.exec())
