import re
import threading

import pandas as pd
from io import StringIO
import time

# Для обычных статических страниц
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Для динамических JS-страниц
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# === Configuration ===
URLS = []  # Добавте ссылки на свои списки
APPLICANT_ID = ''  # Замените на свой ID абитуриента
TIMEOUT = 10  # Таймаут для HTTP-запросов
NEED_TABLE = False # Таблица абитуриентов
LOGGING_ENABLED = False # Логирование

# === Настройка сессии requests с ретраями ===
session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=['GET']
)
session.mount('https://', HTTPAdapter(max_retries=retries))
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
})

# === Логирование ===
def log(msg):
    if LOGGING_ENABLED:
        print(msg)

def fetch_with_selenium(url, max_timeout=30):
    html_result = {'html': None}
    attempt = 1

    def selenium_task(result_container):
        log(f"[INFO] [{url}] [{attempt}] Запускаю headless браузер Selenium")
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        driver = webdriver.Chrome(options=options)
        try:
            driver.get(url)
            log("[INFO] Ожидаю загрузку страницы и интерфейса...")
            wait = WebDriverWait(driver, 15)

            # Найдём и кликнем на "Приоритет №1"
            log("[INFO] Ищу переключатель 'Приоритет №1'...")
            priority_toggle = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//label[contains(., 'Приоритет №1')]")
            ))

            driver.execute_script("arguments[0].scrollIntoView(true);", priority_toggle)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", priority_toggle)
            log("[INFO] Клик через JS прошёл успешно.")

            # Ждём таблицу
            log("[INFO] Жду загрузку таблицы...")
            wait.until(EC.presence_of_element_located((By.XPATH, "//table//tr")))
            time.sleep(2)

            result_container['html'] = driver.page_source
            log("[INFO] HTML успешно получен")
        except Exception as e:
            log(f"[ERROR] Selenium ошибка: {e}")
        finally:
            driver.quit()

    # Повторяем пока не получим HTML или не пройдёт max_timeout
    while html_result['html'] is None:
        log(f"[INFO] [{url}] Попытка #{attempt}")
        thread = threading.Thread(target=selenium_task, args=(html_result,))
        thread.start()
        thread.join(timeout=max_timeout)

        if thread.is_alive():
            log(f"[WARN] [{url}] Таймаут {max_timeout}с: перезапуск парсинга")
            # Поток подвис — дадим ему умереть и перезапустим
            attempt += 1
            continue

        if html_result['html']:
            return html_result['html']

        # Если произошла ошибка, но поток завершился — делаем задержку перед новой попыткой
        log(f"[WARN] [{url}] Попытка не удалась, жду перед перезапуском...")
        time.sleep(2)
        attempt += 1

    raise RuntimeError(f"[ERROR] [{url}] Не удалось получить HTML после {attempt} попыток")

# === Парсинг таблицы в DataFrame ===
def parse_applicants(html):
    log("[INFO] Парсинг HTML для извлечения таблиц через pandas")
    df_list = pd.read_html(StringIO(html))
    log(f"[INFO] Найдено таблиц: {len(df_list)}")
    df = df_list[0]
    df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]
    log(f"[INFO] Всего строк в таблице: {len(df)}")
    return df

# === Фильтрация ===
def filter_applicants(df, applicant_id):
    log(f"[INFO] Фильтрация заявлений: приоритет=1 или id={applicant_id}")
    df['priority'] = pd.to_numeric(df['priority'], errors='coerce')
    mask = (df['№'] == 1) | (df['уникальный_код_поступающего'].astype(str) == str(applicant_id))
    filtered = df.loc[mask]
    log(f"[INFO] После фильтрации осталось: {len(filtered)} строк")
    return filtered

# === Main ===
if __name__ == '__main__':
    log("[INFO] Запуск скрипта фильтрации абитуриентов")
    print("================================================")
    for URL in URLS:
        try:
            html = fetch_with_selenium(URL)
            df = parse_applicants(html)

            mask = (df['приоритет_№'] == 1) | (df['уникальный_код_поступающего'].astype(str) == str(APPLICANT_ID))
            filtered = df.loc[mask].copy()

            filtered.insert(0, 'позиция_в_списке', range(1, len(filtered) + 1))
            filtered.loc[:, 'указатель'] = filtered['уникальный_код_поступающего'].astype(str).apply(
                lambda x: '← это вы' if x == str(APPLICANT_ID) else ' '
            )

            pattern = re.compile(r'<h2\b[^>]*style\s*=\s*["\']color:\s*#0152a3["\'][^>]*>(.*?)</h2>', re.DOTALL)
            match = pattern.search(str(html))
            print("Направление:", match.group(1))

            if NEED_TABLE:
              print(filtered[['позиция_в_списке', '№', 'уникальный_код_поступающего', 'приоритет_№',
                        'конкурсный_балл', 'условия_зачисления', 'согласие_на_зачисление', 'указатель']].to_string(index=False))

            my_row = filtered[filtered['уникальный_код_поступающего'].astype(str) == str(APPLICANT_ID)]
            my_position = None
            applicant_priority = None
            if not my_row.empty:
                my_position = int(my_row['позиция_в_списке'].values[0])
                applicant_priority = my_row.iloc[0]['приоритет_№']
            else:
                log("[WARN] Ты не найден в таблице после фильтрации.")

            # Вывод статистики
            print(f"Всего мест бюджетных мест: {re.search(r'Бюджетных мест:\s*(\S{1,3})', str(html)).group(1)}; "
                  f"Всего мест: {len(filtered)}; "
                  f"Твоё место: {my_position}; "
                  f"Приоритет: {applicant_priority}")

            print("================================================")
        except Exception as e:
            log(f"[ERROR] Сбой при получении или обработке списка: {e}")
