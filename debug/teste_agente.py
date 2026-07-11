import os
from run_agent import AIAgent

print("[SISTEMA] A acordar o Hermes Agent Oficial...")

# Agora a inicialização é limpa porque o 'hermes setup' já fez o trabalho sujo
try:
    # Ao iniciar sem passar parâmetros, ele lê o ficheiro de configuração que acabaste de criar
    agente = AIAgent(
        model="qwen-agent", 
        quiet_mode=True # Mantém o terminal limpo de logs excessivos
    )

    prompt = (
        "Olá R2D2! Usa as tuas ferramentas de sistema do Hermes para criar "
        "um ficheiro chamado 'hermes_oficial.txt' com a mensagem: "
        "'A framework oficial do Hermes Agent está configurada e operacional.' "
        "Avisa-me quando estiver pronto."
    )

    print("\n[A enviar pedido para o teu Linux via framework oficial...]")
    
    resposta = agente.chat(prompt)
    
    print("\n--- RESPOSTA DA IA ---")
    print(resposta)
    print("----------------------\n")
    
    if os.path.exists("hermes_oficial.txt"):
        print(">>> SUCESSO! A framework oficial escreveu o ficheiro com as suas próprias ferramentas.")
    else:
        print(">>> A IA respondeu, mas o ficheiro não foi criado.")

except Exception as e:
    print(f"\n[ERRO NA FRAMEWORK]: {e}")