import os
import webbrowser
import requests
from dotenv import load_dotenv
from urllib.parse import urlencode, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- Конфигурация ---
# Загружаем переменные окружения из файла .env
load_dotenv()

CLIENT_ID = os.getenv("OSM_CLIENT_ID")
CLIENT_SECRET = os.getenv("OSM_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
# ВАЖНО: Убедитесь, что в файле .env указан ваш логин (display name) в OSM, а не email
OSM_USERNAME = os.getenv("OSM_USERNAME") 
GPX_FILE_PATH = "activity_19987887832.gpx" # Имя вашего GPX файла

# Проверяем, что все переменные загружены
if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
    print("Ошибка: Убедитесь, что вы создали .env файл и заполнили OSM_CLIENT_ID, OSM_CLIENT_SECRET и REDIRECT_URI.")
    exit()

# URL-адреса API OpenStreetMap
OSM_BASE_URL = "https://www.openstreetmap.org"
AUTHORIZATION_URL = f"{OSM_BASE_URL}/oauth2/authorize"
TOKEN_URL = f"{OSM_BASE_URL}/oauth2/token"
UPLOAD_URL = f"{OSM_BASE_URL}/api/0.6/gpx/create"

# Запрашиваемые разрешения (scopes)
SCOPES = "read_gpx write_gpx"

# Переменная для хранения кода авторизации
authorization_code = None

# --- Шаг 1: Получение кода авторизации через локальный сервер ---

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """
    Обработчик HTTP-запросов для перехвата callback от OSM.
    Он извлекает код авторизации из URL.
    """
    def do_GET(self):
        global authorization_code
        # Отправляем успешный ответ браузеру
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        
        response_html = """
        <html>
            <head><title>Авторизация успешна</title></head>
            <body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>
                <h1>Отлично!</h1>
                <p>Код авторизации получен. Можете закрыть эту вкладку и вернуться в консоль.</p>
            </body>
        </html>
        """
        self.wfile.write(response_html.encode("utf-8"))

        # Парсим URL и извлекаем код
        query_params = parse_qs(self.path.split('?', 1)[1])
        code = query_params.get("code", [None])[0]

        if code:
            print("\n[Шаг 1] Успешно получен код авторизации.")
            authorization_code = code
        else:
            print("\n[Шаг 1] Ошибка: Не удалось получить код авторизации.")
            error = query_params.get("error", ["unknown"])[0]
            print(f"  Причина: {error}")


def get_authorization_code():
    """
    Запускает процесс авторизации: генерирует URL, открывает его в браузере
    и запускает локальный сервер для ожидания callback.
    """
    # Формируем параметры для URL авторизации
    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
    }
    # Кодируем параметры в URL
    full_auth_url = f"{AUTHORIZATION_URL}?{urlencode(auth_params)}"

    print("[Шаг 1] Запуск процесса авторизации...")
    print("  Сейчас в вашем браузере откроется страница для подтверждения доступа.")
    print("  Пожалуйста, войдите в свой аккаунт OSM и нажмите 'Grant Access' (Предоставить доступ).")
    
    # Открываем URL в браузере
    webbrowser.open(full_auth_url)

    # Запускаем локальный сервер для ожидания редиректа
    host, port = "127.0.0.1", 8080
    httpd = HTTPServer((host, port), OAuthCallbackHandler)
    print(f"  Локальный сервер запущен на http://{host}:{port} и ожидает ответа от OSM...")
    
    # Обрабатываем один запрос (callback) и останавливаемся
    httpd.handle_request()
    httpd.server_close()
    print("  Локальный сервер остановлен.")


# --- Шаг 2: Обмен кода на токен доступа ---

def get_access_token(code):
    """
    Отправляет POST-запрос для обмена кода авторизации на токен доступа.
    """
    print("\n[Шаг 2] Обмен кода на токен доступа...")
    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    try:
        response = requests.post(TOKEN_URL, data=token_payload)
        response.raise_for_status()  # Проверяем на HTTP ошибки
        access_token = response.json().get("access_token")
        if access_token:
            print("  Токен доступа успешно получен!")
            return access_token
        else:
            print("  Ошибка: В ответе от сервера отсутствует токен доступа.")
            print(f"  Ответ сервера: {response.json()}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"  Произошла ошибка при запросе токена: {e}")
        print(f"  Тело ответа: {e.response.text if e.response else 'Нет ответа'}")
        return None


# --- Шаг 3: Загрузка GPX-файла ---

def upload_gpx_file(token):
    """
    Загружает GPX-файл на сервер OSM, используя токен доступа.
    """
    print("\n[Шаг 3] Загрузка GPX-файла на сервер OpenStreetMap...")
    
    if not os.path.exists(GPX_FILE_PATH):
        print(f"  Ошибка: GPX-файл не найден по пути '{GPX_FILE_PATH}'")
        return

    # Подготавливаем данные для multipart/form-data запроса
    files = {
        # 'file': (имя_файла, содержимое_файла, тип_контента)
        'file': (os.path.basename(GPX_FILE_PATH), open(GPX_FILE_PATH, 'rb'), 'application/gpx+xml')
    }
    data = {
        'description': 'Тестовая загрузка трека через API',
        'tags': 'test, api',
        'visibility': 'private' # Возможные значения: private, public, trackable, identifiably
    }
    headers = {
        'Authorization': f'Bearer {token}'
    }

    try:
        response = requests.post(UPLOAD_URL, files=files, data=data, headers=headers)
        response.raise_for_status()
        gpx_id = response.text
        print("\n--- УСПЕХ! ---")
        print(f"  GPX-файл успешно загружен! ID вашего трека: {gpx_id}")

        # Формируем URL для просмотра трека
        if OSM_USERNAME and '@' not in OSM_USERNAME:
            print(f"  Вы можете найти его здесь: {OSM_BASE_URL}/user/{OSM_USERNAME}/traces/{gpx_id}")
            print("  (Может потребоваться несколько минут, чтобы трек появился в списке)")
        else:
            print(f"\n  [ВАЖНО] Чтобы увидеть трек, перейдите на страницу своих треков в OSM:")
            print(f"  > {OSM_BASE_URL}/user/YOUR_USERNAME/traces/{gpx_id}")
            print("  Замените 'YOUR_USERNAME' на ваш логин в OpenStreetMap (не email).")
            print("  Мы не смогли сформировать точную ссылку, так как в .env файле не указан или неверно указан OSM_USERNAME.")


    except requests.exceptions.RequestException as e:
        print("\n--- ОШИБКА ---")
        print(f"  Не удалось загрузить файл: {e}")
        print(f"  Код ответа: {e.response.status_code if e.response else 'N/A'}")
        print(f"  Тело ответа: {e.response.text if e.response else 'Нет ответа'}")


# --- Основной процесс ---

if __name__ == "__main__":
    # Шаг 1
    get_authorization_code()

    # Шаг 2
    if authorization_code:
        access_token = get_access_token(authorization_code)
        
        # Шаг 3
        if access_token:
            upload_gpx_file(access_token)
    else:
        print("\nПроцесс прерван, так как не удалось получить код авторизации.")
