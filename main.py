# main.py
import threading
import time
import webview
import os
import sys
from app import app

# Função para obter caminho de recursos (necessária para PyInstaller)
def resource_path(relative_path):
    """ Obtém o caminho absoluto para o recurso, funciona em desenvolvimento e com PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def start_server():
    """Inicia o servidor com waitress"""
    from waitress import serve
    print("Servidor rodando em http://127.0.0.1:5000")
    serve(app, host='127.0.0.1', port=5000)

# Função para evitar abrir links em navegador externo
def on_new_window(url):
    window.load_url(url)
    return False  # Impede ação padrão

if __name__ == '__main__':
    # Inicia o servidor em uma thread
    t = threading.Thread(target=start_server)
    t.daemon = True
    t.start()
    time.sleep(1)  # Espera o servidor iniciar

    # Cria a janela do sistema
    window = webview.create_window(
        'Gestão de Combustível',
        'http://127.0.0.1:5000',
        width=1200,
        height=800,
        resizable=True,
        min_size=(1000, 700)
    )

    # Inicia o webview sem abrir navegador externo
    webview.start(debug=False)
