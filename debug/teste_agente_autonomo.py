import os
import json
from openai import OpenAI

# ==========================================
# 1. SETUP DE COMUNICAÇÃO LIGEIRO
# ==========================================
LINUX_IP = "192.168.1.192"
print(f"[SISTEMA] A contactar nó de inferência em {LINUX_IP}...")

# Usamos apenas o cliente base da OpenAI, apontando para o teu Ollama
cliente = OpenAI(
    base_url=f"http://{LINUX_IP}:11434/v1",
    api_key="ollama" # Requisito dummy
)

# ==========================================
# 2. DEFINIÇÃO DA FERRAMENTA (Nativo OpenAI)
# ==========================================
# É assim que se descreve uma ferramenta sem frameworks pesadas
tools_disponiveis = [
    {
        "type": "function",
        "function": {
            "name": "gravar_relatorio_local",
            "description": "Grava uma mensagem de texto num ficheiro local chamado 'relatorio_r2d2.txt'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "texto": {
                        "type": "string",
                        "description": "A mensagem exata a ser gravada no ficheiro."
                    }
                },
                "required": ["texto"]
            }
        }
    }
]

def executar_gravar_relatorio(texto):
    """A função real em Python que o teu portátil vai executar."""
    with open("relatorio_r2d2.txt", "a", encoding="utf-8") as f:
        f.write(f"- {texto}\n")
    return "Ficheiro gravado com sucesso."

# ==========================================
# 3. O CICLO DE EXECUÇÃO (O Cérebro)
# ==========================================
if __name__ == "__main__":
    prompt_teste = "Olá! Por favor, grava a seguinte mensagem de diagnóstico: 'Comunicação API direta estabelecida com sucesso, sem bloatware.'"
    
    mensagens = [{"role": "user", "content": prompt_teste}]
    
    print("\n[A enviar pedido para o Qwen-Agent no Linux...]")
    
    try:
        # A chamada limpa à API
        resposta = cliente.chat.completions.create(
            model="qwen-agent",
            messages=mensagens,
            tools=tools_disponiveis,
            temperature=0.1
        )
        
        mensagem_ia = resposta.choices[0].message
        
        # O modelo decidiu usar uma tool?
        if mensagem_ia.tool_calls:
            print("\n>>> O modelo decidiu usar uma ferramenta!")
            
            for tool_call in mensagem_ia.tool_calls:
                nome_funcao = tool_call.function.name
                # Converte os argumentos (que vêm em formato string JSON) para dicionário Python
                argumentos = json.loads(tool_call.function.arguments)
                
                print(f"-> A executar: {nome_funcao}({argumentos})")
                
                if nome_funcao == "gravar_relatorio_local":
                    resultado = executar_gravar_relatorio(argumentos["texto"])
                    print(f"-> Resultado local: {resultado}")
                    
            if os.path.exists("relatorio_r2d2.txt"):
                print("\n[VEREDITO] SUCESSO! Ficheiro criado nativamente.")
                
        else:
            print("\nO modelo respondeu com texto em vez de usar a ferramenta:")
            print(mensagem_ia.content)

    except Exception as e:
        print(f"\n[ERRO DE LIGAÇÃO] Falha na API: {e}")