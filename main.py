
import os
import json
import threading
import secrets
from pathlib import Path
from functools import wraps

import discord
from discord.ext import commands
from flask import Flask, render_template, request, redirect, url_for, session, flash

# =========================================================
# CONFIGURAÇÕES DO SITE
# =========================================================

PANEL_PASSWORD = os.getenv("PANEL_PASSWORD")
if not PANEL_PASSWORD:
    raise RuntimeError(
        "A variável PANEL_PASSWORD não foi configurada. "
        "Crie essa variável no Railway antes de iniciar."
    )

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "menu_config.json"

DEFAULT_CONFIG = {
    "titulo": "📋 Menu do Servidor",
    "descricao": "Escolha uma das opções abaixo.",
    "cor": "5865F2",
    "botoes": [
        {
            "emoji": "⭐",
            "nome": "Vantagens do Sub Civil",
            "resposta": "🎵 Efeitos sonoros\n📹 Abrir câmera\n🖥️ Transmitir tela\n🚀 Ignorar modo lento\n🎨 Cor exclusiva\n⭐ Cargo destacado\n🔊 Prioridade de voz\n💬 Chat exclusivo"
        },
        {
            "emoji": "💬",
            "nome": "Por onde interagir?",
            "resposta": "A interação é feita pelo chat do servidor. A Loritta reconhece as mensagens e contabiliza a atividade."
        },
        {
            "emoji": "🏆",
            "nome": "Como saber quem mais interagiu?",
            "resposta": "Reinicie o XP 5 minutos antes do início. Ao final, confira o ranking da Loritta; quem estiver no topo vence."
        }
    ]
}

_config_lock = threading.Lock()


def carregar_config():
    with _config_lock:
        if not CONFIG_FILE.exists():
            salvar_config_sem_lock(DEFAULT_CONFIG)
        with CONFIG_FILE.open("r", encoding="utf-8") as arquivo:
            return json.load(arquivo)


def salvar_config_sem_lock(config):
    temporario = CONFIG_FILE.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(config, arquivo, ensure_ascii=False, indent=2)
    temporario.replace(CONFIG_FILE)


def salvar_config(config):
    with _config_lock:
        salvar_config_sem_lock(config)


# =========================================================
# SITE / PAINEL WEB
# =========================================================

app = Flask(__name__)
app.secret_key = os.getenv("PANEL_SECRET_KEY") or secrets.token_hex(32)


def login_obrigatorio(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logado"):
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        senha = request.form.get("senha", "")
        if secrets.compare_digest(senha, PANEL_PASSWORD):
            session["logado"] = True
            return redirect(url_for("painel"))
        flash("Senha incorreta.")
    return render_template("login.html")


@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_obrigatorio
def painel():
    config = carregar_config()

    if request.method == "POST":
        titulo = request.form.get("titulo", "").strip()
        descricao = request.form.get("descricao", "").strip()
        cor = request.form.get("cor", "5865F2").replace("#", "").strip().upper()

        if len(cor) != 6:
            flash("A cor precisa ter 6 caracteres, por exemplo: 5865F2.")
            return redirect(url_for("painel"))

        try:
            int(cor, 16)
        except ValueError:
            flash("Cor inválida. Use apenas números 0-9 e letras A-F.")
            return redirect(url_for("painel"))

        botoes = []
        for i in range(3):
            botoes.append({
                "emoji": request.form.get(f"emoji_{i}", "").strip(),
                "nome": request.form.get(f"nome_{i}", "").strip()[:80],
                "resposta": request.form.get(f"resposta_{i}", "").strip()[:4000]
            })

        novo_config = {
            "titulo": titulo[:256],
            "descricao": descricao[:4000],
            "cor": cor,
            "botoes": botoes
        }

        salvar_config(novo_config)
        flash("✅ Alterações salvas. O próximo /menu já usará essas configurações.")
        return redirect(url_for("painel"))

    return render_template("index.html", config=config)


@app.route("/status")
def status():
    return {"site": "online", "bot_configurado": bool(os.getenv("TOKEN"))}, 200


# =========================================================
# BOT DO DISCORD
# =========================================================

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


class MenuView(discord.ui.View):
    def __init__(self, config):
        super().__init__(timeout=300)

        for botao_config in config.get("botoes", [])[:3]:
            nome = botao_config.get("nome") or "Opção"
            emoji = botao_config.get("emoji") or None
            resposta = botao_config.get("resposta") or "Sem conteúdo configurado."

            botao = discord.ui.Button(
                label=nome[:80],
                emoji=emoji,
                style=discord.ButtonStyle.primary
            )

            async def callback(
                interaction: discord.Interaction,
                texto=resposta,
                titulo_botao=nome
            ):
                embed_resposta = discord.Embed(
                    title=titulo_botao,
                    description=texto,
                    color=discord.Color.blurple()
                )
                await interaction.response.send_message(
                    embed=embed_resposta,
                    ephemeral=True
                )

            botao.callback = callback
            self.add_item(botao)


@bot.tree.command(name="menu", description="Abre o menu principal do servidor.")
async def menu(interaction: discord.Interaction):
    config = carregar_config()

    try:
        cor = int(config.get("cor", "5865F2"), 16)
    except ValueError:
        cor = 0x5865F2

    embed = discord.Embed(
        title=config.get("titulo") or "Menu",
        description=config.get("descricao") or "Escolha uma opção.",
        color=cor
    )

    await interaction.response.send_message(
        embed=embed,
        view=MenuView(config)
    )


@bot.event
async def on_ready():
    if getattr(bot, "_menu_sync_feito", False):
        print(f"Bot conectado como {bot.user}")
        return

    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            comandos = await bot.tree.sync(guild=guild)
            print(f"{len(comandos)} comando(s) sincronizado(s) no servidor {GUILD_ID}.")
        else:
            comandos = await bot.tree.sync()
            print(f"{len(comandos)} comando(s) global(is) sincronizado(s).")
        bot._menu_sync_feito = True
    except Exception as erro:
        print(f"Erro ao sincronizar comandos: {erro}")

    print(f"Bot conectado como {bot.user}")


def iniciar_bot():
    if not TOKEN:
        print("AVISO: TOKEN não configurado. O site abrirá, mas o bot não ficará online.")
        return

    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    thread_bot = threading.Thread(target=iniciar_bot, daemon=True)
    thread_bot.start()

    porta = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=porta, debug=False, use_reloader=False)
