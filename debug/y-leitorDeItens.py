import cv2
import pytesseract
import numpy as np
import mss

# Se estiveres no Windows, precisas indicar onde o Tesseract está instalado:
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def read_market_list():
    # Coordenadas aproximadas da lista no painel esquerdo (ajusta conforme necessário)
    monitor_market = {"top": 400, "left": 100, "width": 400, "height": 500}
    
    with mss.mss() as sct:
        raw_img = np.array(sct.grab(monitor_market))
        img = cv2.cvtColor(raw_img, cv2.COLOR_BGRA2GRAY) # Texto lê-se melhor em cinza
        
        # Threshold para deixar o texto branco e o fundo preto (binarização)
        _, thresh = cv2.threshold(img, 150, 255, cv2.THRESH_BINARY)
        
        # Extrair texto
        text = pytesseract.image_to_string(thresh)
        
        print("--- TEXTO DETETADO ---")
        print(text)
        
        if "Fujin Tea" in text:
            print(">>> ITEM ENCONTRADO: Fujin Tea está na lista!")
            return True
    return False

# Teste rápido
if __name__ == "__main__":
    read_market_list()