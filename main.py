import asyncio
import os
import json
import random
import re
import threading
import secrets
import urllib.parse
import urllib.request
import urllib.error
import unicodedata
from copy import deepcopy
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

# =========================================================
# CONFIGURAÇÕES DO SITE
# =========================================================

PANEL_PASSWORD = os.getenv("PANEL_PASSWORD")
if not PANEL_PASSWORD:
    raise RuntimeError(
        "A variável PANEL_PASSWORD não foi configurada. "
        "Crie essa variável no Railway antes de iniciar."
    )

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Novo arquivo: vários menus, um por canal.
MENUS_FILE = DATA_DIR / "menus_config.json"

# Arquivo antigo. Se existir, o painel consegue aproveitar a configuração
# como modelo inicial sem apagar os dados antigos.
CONFIG_ANTIGO_FILE = DATA_DIR / "menu_config.json"

# Canal usado apenas para prévias/testes enviados pelo painel.
CANAL_TESTE_ID = 1537936115233722388

# Permissões do /menu deste serviço.
DONO_ID = 1455937306400653344
CARGO_DESENVOLVIMENTO_ID = 1533625836874498181
MAX_BOTOES = 25
# =========================================================
# PERFIL ROBLOX ↔ DISCORD PARA O JOGO
# =========================================================
MAIN_DISCORD_GUILD_ID = int(os.getenv("MAIN_DISCORD_GUILD_ID", "1532613054703997012") or "1532613054703997012")
EVENTOS_GUILD_ID = int(os.getenv("EVENTOS_GUILD_ID", "1541541588122079283") or "1541541588122079283")
ROBLOX_OWNER_USER_ID = int(os.getenv("ROBLOX_OWNER_USER_ID", "8863543599") or "8863543599")
ROBLOX_LINKS_FILE = DATA_DIR / os.getenv("ROBLOX_LINKS_FILENAME", "roblox_links.json")
MAIN_ROLE_PRIORITY = [
    (1532613934883016704, "ADM_G"),
    (1540876101763600424, "CF_DPT"),
    (1533624911912767629, "ADM_DC"),
    (1540987356520251482, "MOD_DC"),
    (1532614113346453724, "MEM"),
]
EVENT_ROLE_PRIORITY = [
    (1541624067256352868, "CF_DPT_EVT"),
    (1541624066396651580, "DIR_EVT"),
    (1541624065054482472, "GER_EVT"),
    (1541624064081268878, "COO_EVT"),
    (1541624062910922843, "SUP_EVT"),
    (1541624062298685530, "AP_EVT"),
]


DEFAULT_MENU = {
    "titulo": "📋 Menu do Servidor",
    "descricao": "Escolha uma das opções abaixo.",
    "cor": "5865F2",
    "botoes": [
        {
            "emoji": "⭐",
            "nome": "Opção 1",
            "resposta": "Configure a resposta desta opção pelo painel."
        },
        {
            "emoji": "💬",
            "nome": "Opção 2",
            "resposta": "Configure a resposta desta opção pelo painel."
        },
        {
            "emoji": "🏆",
            "nome": "Opção 3",
            "resposta": "Configure a resposta desta opção pelo painel."
        }
    ]
}

_config_lock = threading.Lock()


def estrutura_vazia():
    return {
        "versao": 2,
        "menus": {}
    }


def salvar_menus_sem_lock(dados):
    temporario = MENUS_FILE.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
    temporario.replace(MENUS_FILE)


def carregar_menus():
    with _config_lock:
        if not MENUS_FILE.exists():
            salvar_menus_sem_lock(estrutura_vazia())

        try:
            with MENUS_FILE.open("r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (OSError, json.JSONDecodeError):
            dados = estrutura_vazia()
            salvar_menus_sem_lock(dados)

        if not isinstance(dados, dict):
            dados = estrutura_vazia()

        if not isinstance(dados.get("menus"), dict):
            dados["menus"] = {}

        dados.setdefault("versao", 2)
        return dados


def salvar_menus(dados):
    with _config_lock:
        salvar_menus_sem_lock(dados)


def carregar_config_antiga():
    if not CONFIG_ANTIGO_FILE.exists():
        return None

    try:
        with CONFIG_ANTIGO_FILE.open("r", encoding="utf-8") as arquivo:
            config = json.load(arquivo)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(config, dict):
        return None

    if not isinstance(config.get("botoes"), list):
        return None

    return config


def menu_padrao():
    antigo = carregar_config_antiga()
    if antigo:
        config = deepcopy(DEFAULT_MENU)
        config.update({
            "titulo": antigo.get("titulo", config["titulo"]),
            "descricao": antigo.get("descricao", config["descricao"]),
            "cor": antigo.get("cor", config["cor"]),
            "botoes": antigo.get("botoes", config["botoes"])[:MAX_BOTOES]
        })
        if not config["botoes"]:
            config["botoes"] = deepcopy(DEFAULT_MENU["botoes"])
        return config

    return deepcopy(DEFAULT_MENU)


def normalizar_menu(config):
    base = menu_padrao()

    if not isinstance(config, dict):
        return base

    base["titulo"] = str(config.get("titulo") or base["titulo"])[:256]
    base["descricao"] = str(config.get("descricao") or base["descricao"])[:4000]
    base["cor"] = str(config.get("cor") or base["cor"]).replace("#", "").upper()[:6]

    botoes_recebidos = config.get("botoes")
    if isinstance(botoes_recebidos, list):
        botoes = []
        for atual in botoes_recebidos[:MAX_BOTOES]:
            if not isinstance(atual, dict):
                continue
            botoes.append({
                "emoji": str(atual.get("emoji") or "")[:100],
                "nome": str(atual.get("nome") or "Opção")[:80],
                "resposta": str(atual.get("resposta") or "Sem conteúdo configurado.")[:4000]
            })
        if botoes:
            base["botoes"] = botoes

    return base


def config_do_formulario(form):
    titulo = form.get("titulo", "").strip()
    descricao = form.get("descricao", "").strip()
    cor = form.get("cor", "D4AF37").replace("#", "").strip().upper()

    if len(cor) != 6:
        raise ValueError("A cor precisa ter 6 caracteres, por exemplo: D4AF37.")

    try:
        int(cor, 16)
    except ValueError as exc:
        raise ValueError("Cor inválida. Use apenas números 0-9 e letras A-F.") from exc

    try:
        quantidade = int(form.get("quantidade_botoes", "1"))
    except ValueError:
        quantidade = 1

    quantidade = max(1, min(quantidade, MAX_BOTOES))
    botoes = []
    for i in range(quantidade):
        botoes.append({
            "emoji": form.get(f"emoji_{i}", "").strip()[:100],
            "nome": (form.get(f"nome_{i}", "").strip() or f"Opção {i + 1}")[:80],
            "resposta": (form.get(f"resposta_{i}", "").strip() or "Sem conteúdo configurado.")[:4000]
        })

    return {
        "titulo": titulo[:256],
        "descricao": descricao[:4000],
        "cor": cor,
        "botoes": botoes
    }


# =========================================================
# BOT DO DISCORD
# =========================================================

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
BOT_LOOP = None

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def cor_da_config(config):
    try:
        return int(config.get("cor", "5865F2"), 16)
    except (ValueError, TypeError):
        return 0x5865F2


def pode_usar_menu(membro):
    if not isinstance(membro, discord.Member):
        return False

    if membro.id == DONO_ID:
        return True

    return any(
        cargo.id == CARGO_DESENVOLVIMENTO_ID
        for cargo in membro.roles
    )


class MenuView(discord.ui.View):
    """Menu oficial persistente do Discord."""

    def __init__(
        self,
        config,
        canal_id,
        *,
        persistente=True
    ):
        super().__init__(
            timeout=None if persistente else 300
        )

        self.canal_id = str(canal_id)

        for indice, botao_config in enumerate(
            config.get("botoes", [])[:MAX_BOTOES]
        ):
            nome = botao_config.get("nome") or "Opção"
            emoji = botao_config.get("emoji") or None
            resposta = (
                botao_config.get("resposta")
                or "Sem conteúdo configurado."
            )

            argumentos = {
                "label": nome[:80],
                "emoji": emoji,
                "style": discord.ButtonStyle.primary,
                "row": indice // 5
            }

            if persistente:
                argumentos["custom_id"] = (
                    f"rm_menu:{self.canal_id}:{indice}"
                )

            botao = discord.ui.Button(**argumentos)

            async def callback(
                interaction: discord.Interaction,
                texto=resposta,
                titulo_botao=nome
            ):
                embed_resposta = discord.Embed(
                    title=titulo_botao,
                    description=texto,
                    color=discord.Color.gold()
                )
                await interaction.response.send_message(
                    embed=embed_resposta,
                    ephemeral=True
                )

            botao.callback = callback
            self.add_item(botao)


def registrar_views_persistentes():
    """Reativa os botões dos menus salvos após reinício/deploy."""
    if getattr(bot, "_views_menus_registradas", False):
        return

    dados = carregar_menus()
    total = 0

    for canal_id, config in dados["menus"].items():
        try:
            bot.add_view(
                MenuView(
                    normalizar_menu(config),
                    canal_id,
                    persistente=True
                )
            )
            total += 1
        except Exception as erro:
            print(
                f"Erro ao registrar menu persistente "
                f"do canal {canal_id}: {erro}"
            )

    bot._views_menus_registradas = True
    print(f"Views persistentes registradas: {total}")


async def obter_canais_texto_discord():
    if not bot.is_ready():
        return []

    guild = None

    if GUILD_ID:
        try:
            guild = bot.get_guild(int(GUILD_ID))
        except ValueError:
            guild = None

    if guild is None and bot.guilds:
        guild = bot.guilds[0]

    if guild is None:
        return []

    canais = []

    for canal in guild.text_channels:
        canais.append({
            "id": str(canal.id),
            "nome": canal.name,
            "categoria": canal.category.name if canal.category else "Sem categoria",
            "posicao": canal.position
        })

    canais.sort(key=lambda item: (item["categoria"].lower(), item["posicao"], item["nome"].lower()))
    return canais


def obter_canais_texto_sync():
    if not TOKEN or not bot.is_ready() or BOT_LOOP is None:
        return []

    try:
        futuro = asyncio.run_coroutine_threadsafe(
            obter_canais_texto_discord(),
            BOT_LOOP
        )
        return futuro.result(timeout=10)
    except Exception as erro:
        print(f"Erro ao obter canais do Discord: {repr(erro)}")
        return []


async def localizar_canal(canal_id):
    try:
        canal_id_int = int(canal_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ID de canal inválido.") from exc

    canal = bot.get_channel(canal_id_int)

    if canal is None:
        try:
            canal = await bot.fetch_channel(canal_id_int)
        except discord.NotFound as exc:
            raise RuntimeError("Canal não encontrado.") from exc
        except discord.Forbidden as exc:
            raise RuntimeError("O bot não tem acesso ao canal.") from exc
        except discord.HTTPException as exc:
            raise RuntimeError(f"Erro do Discord ao localizar o canal: {exc}") from exc

    if not hasattr(canal, "send"):
        raise RuntimeError("O canal configurado não aceita mensagens.")

    return canal


async def enviar_teste_discord(config):
    canal = await localizar_canal(CANAL_TESTE_ID)

    embed = discord.Embed(
        title=config.get("titulo") or "Menu",
        description=config.get("descricao") or "Escolha uma opção.",
        color=cor_da_config(config)
    )

    await canal.send(
        content="⚠️ **PRÉVIA / TESTE — não substitui nenhum menu oficial**",
        embed=embed,
        view=MenuView(
            config,
            f"teste_{secrets.token_hex(4)}",
            persistente=False
        )
    )


@bot.tree.command(
    name="menu",
    description="Envia o menu configurado para este canal."
)
async def menu(interaction: discord.Interaction):
    if not pode_usar_menu(interaction.user):
        await interaction.response.send_message(
            "❌ Você não possui autorização para publicar menus.",
            ephemeral=True
        )
        return

    dados = carregar_menus()
    config = dados["menus"].get(str(interaction.channel_id))

    if not config:
        await interaction.response.send_message(
            "❌ Não existe um menu configurado para este canal.\n"
            "Configure este canal primeiro pelo painel web.",
            ephemeral=True
        )
        return

    config = normalizar_menu(config)

    embed = discord.Embed(
        title=config.get("titulo") or "Menu",
        description=config.get("descricao") or "Escolha uma opção.",
        color=cor_da_config(config)
    )

    await interaction.response.send_message(
        embed=embed,
        view=MenuView(
            config,
            str(interaction.channel_id),
            persistente=True
        )
    )



def agora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def carregar_entradas():
    if not ENTRADAS_FILE.exists():
        return []

    try:
        with ENTRADAS_FILE.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, json.JSONDecodeError):
        return []

    return dados if isinstance(dados, list) else []


def salvar_entradas(dados):
    with _entradas_lock:
        temporario = ENTRADAS_FILE.with_suffix(".tmp")
        with temporario.open("w", encoding="utf-8") as arquivo:
            json.dump(dados[-5000:], arquivo, ensure_ascii=False, indent=2)
        temporario.replace(ENTRADAS_FILE)


def registrar_entrada(registro):
    dados = carregar_entradas()
    dados.append(registro)
    salvar_entradas(dados)


async def atualizar_cache_convites(guild):
    try:
        convites = await guild.invites()
        _invites_cache[guild.id] = {
            convite.code: convite.uses or 0
            for convite in convites
        }
    except (discord.Forbidden, discord.HTTPException):
        _invites_cache.setdefault(guild.id, {})

    try:
        vanity = await guild.vanity_invite()
        if vanity:
            _vanity_cache[guild.id] = {
                "code": vanity.code,
                "uses": vanity.uses or 0,
            }
    except (discord.Forbidden, discord.HTTPException):
        _vanity_cache.pop(guild.id, None)


async def descobrir_origem_entrada(guild):
    anterior = _invites_cache.get(guild.id, {})

    try:
        atuais = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        atuais = []

    usado = None
    for convite in atuais:
        usos_antes = anterior.get(convite.code, 0)
        usos_agora = convite.uses or 0
        if usos_agora > usos_antes:
            usado = convite
            break

    _invites_cache[guild.id] = {
        convite.code: convite.uses or 0
        for convite in atuais
    }

    if usado is not None:
        convidador = usado.inviter
        return {
            "origem": "convite",
            "convite_codigo": usado.code,
            "convidador_id": str(convidador.id) if convidador else "",
            "convidador_nome": str(convidador) if convidador else "Desconhecido",
            "detalhe": f"Convite {usado.code}",
        }

    # Vanity URL é detectável separadamente quando o servidor possui uma.
    anterior_vanity = _vanity_cache.get(guild.id)
    try:
        vanity = await guild.vanity_invite()
    except (discord.Forbidden, discord.HTTPException):
        vanity = None

    if vanity:
        usos_agora = vanity.uses or 0
        usos_antes = (anterior_vanity or {}).get("uses", usos_agora)
        _vanity_cache[guild.id] = {"code": vanity.code, "uses": usos_agora}
        if usos_agora > usos_antes:
            return {
                "origem": "vanity",
                "convite_codigo": vanity.code,
                "convidador_id": "",
                "convidador_nome": "",
                "detalhe": f"Link personalizado /{vanity.code}",
            }

    return {
        "origem": "desconhecida",
        "convite_codigo": "",
        "convidador_id": "",
        "convidador_nome": "",
        "detalhe": "O Discord não informou qual origem foi usada.",
    }


@bot.event
async def on_member_join(member):
    origem = await descobrir_origem_entrada(member.guild)

    registrar_entrada({
        "data": agora_iso(),
        "membro_id": str(member.id),
        "membro_nome": str(member),
        "membro_exibicao": member.display_name,
        **origem,
    })


@bot.event
async def on_invite_create(invite):
    if invite.guild:
        await atualizar_cache_convites(invite.guild)


@bot.event
async def on_invite_delete(invite):
    if invite.guild:
        await atualizar_cache_convites(invite.guild)


@bot.event
async def on_ready():
    global BOT_LOOP
    BOT_LOOP = asyncio.get_running_loop()

    for guild in bot.guilds:
        await atualizar_cache_convites(guild)

    registrar_views_persistentes()
    registrar_views_estruturas_persistentes()

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



# =========================================================
# CONSTRUTOR DE ESTRUTURAS / CATEGORIAS DO DISCORD
# =========================================================

ESTILOS_ESTRUTURA = {
    "rm": "Resenha Máxima — 𝑬𝑽𝑬𝑵𝑻𝑶𝑺",
    "negrito": "Negrito — 𝐄𝐕𝐄𝐍𝐓𝐎𝐒",
    "normal": "Normal — EVENTOS",
}

_ASCII_MAIUSCULO = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ASCII_MINUSCULO = "abcdefghijklmnopqrstuvwxyz"
_MAT_BOLD_MAIUSCULO = "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙"
_MAT_BOLD_MINUSCULO = "𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳"
_MAT_BOLD_ITALIC_MAIUSCULO = "𝑨𝑩𝑪𝑫𝑬𝑭𝑮𝑯𝑰𝑱𝑲𝑳𝑴𝑵𝑶𝑷𝑸𝑹𝑺𝑻𝑼𝑽𝑾𝑿𝒀𝒁"
_MAT_BOLD_ITALIC_MINUSCULO = "𝒂𝒃𝒄𝒅𝒆𝒇𝒈𝒉𝒊𝒋𝒌𝒍𝒎𝒏𝒐𝒑𝒒𝒓𝒔𝒕𝒖𝒗𝒘𝒙𝒚𝒛"

_MAPA_FONTES = {
    "negrito": str.maketrans(
        _ASCII_MAIUSCULO + _ASCII_MINUSCULO,
        _MAT_BOLD_MAIUSCULO + _MAT_BOLD_MINUSCULO,
    ),
    "rm": str.maketrans(
        _ASCII_MAIUSCULO + _ASCII_MINUSCULO,
        _MAT_BOLD_ITALIC_MAIUSCULO + _MAT_BOLD_ITALIC_MINUSCULO,
    ),
}


def _estrutura_vazia():
    return {"versao": 1, "estruturas": {}}


def _salvar_estruturas_sem_lock(dados):
    temporario = ESTRUTURAS_DISCORD_FILE.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
    temporario.replace(ESTRUTURAS_DISCORD_FILE)


def carregar_estruturas_discord():
    with _estruturas_discord_lock:
        if not ESTRUTURAS_DISCORD_FILE.exists():
            _salvar_estruturas_sem_lock(_estrutura_vazia())
        try:
            with ESTRUTURAS_DISCORD_FILE.open("r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (OSError, json.JSONDecodeError):
            dados = _estrutura_vazia()
            _salvar_estruturas_sem_lock(dados)
        if not isinstance(dados, dict):
            dados = _estrutura_vazia()
        if not isinstance(dados.get("estruturas"), dict):
            dados["estruturas"] = {}
        return dados


def salvar_estruturas_discord(dados):
    with _estruturas_discord_lock:
        _salvar_estruturas_sem_lock(dados)


def _sem_acentos(texto):
    normalizado = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(ch for ch in normalizado if not unicodedata.combining(ch))


def estilizar_nome_estrutura(texto, estilo="rm"):
    texto = _sem_acentos(texto).strip()
    texto = re.sub(r"\s+", "-", texto)
    texto = re.sub(r"-{2,}", "-", texto)
    if estilo in _MAPA_FONTES:
        texto = texto.upper().translate(_MAPA_FONTES[estilo])
    elif estilo == "normal":
        texto = texto.upper()
    return texto[:90]


def _nome_com_emoji(emoji, texto, estilo="rm"):
    nome = estilizar_nome_estrutura(texto, estilo)
    return f"{emoji}・{nome}"[:100]


def _ids_cargos(texto):
    ids = []
    for achado in re.findall(r"\d{15,22}", str(texto or "")):
        valor = int(achado)
        if valor not in ids:
            ids.append(valor)
    return ids


def _estrutura_por_categoria(categoria_id):
    dados = carregar_estruturas_discord()
    return dados.get("estruturas", {}).get(str(categoria_id))


def _membro_admin_estrutura(membro, estrutura):
    if not isinstance(membro, discord.Member):
        return False
    if membro.id == DONO_ID or membro.guild_permissions.administrator:
        return True
    permitidos = {
        int(x)
        for x in estrutura.get("admin_role_ids", [])
        if str(x).isdigit()
    }
    return any(role.id in permitidos for role in membro.roles)


class HierarquiaEstruturaView(discord.ui.View):
    def __init__(self, categoria_id):
        super().__init__(timeout=None)
        self.categoria_id = str(categoria_id)
        botao = discord.ui.Button(
            label="Gerenciar Hierarquia",
            emoji="⚙️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"rm_estrutura_hierarquia:{self.categoria_id}",
        )
        botao.callback = self._abrir
        self.add_item(botao)

    async def _abrir(self, interaction: discord.Interaction):
        estrutura = _estrutura_por_categoria(self.categoria_id)
        if not estrutura:
            await interaction.response.send_message(
                "❌ Esta estrutura não está mais registrada no painel.",
                ephemeral=True,
            )
            return
        if not _membro_admin_estrutura(interaction.user, estrutura):
            await interaction.response.send_message(
                "❌ Apenas os administradores configurados podem usar este painel.",
                ephemeral=True,
            )
            return
        admins = (
            ", ".join(
                f"<@&{x}>"
                for x in estrutura.get("admin_role_ids", [])
            )
            or "Somente administradores do servidor"
        )
        await interaction.response.send_message(
            "## 👑 Painel da Hierarquia\n\n"
            "Este canal foi preparado para comandos administrativos da hierarquia.\n"
            f"**Cargos autorizados:** {admins}\n\n"
            "Os membros do departamento podem visualizar o histórico, "
            "mas não enviar mensagens aqui.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def registrar_views_estruturas_persistentes():
    if getattr(bot, "_views_estruturas_registradas", False):
        return
    dados = carregar_estruturas_discord()
    total = 0
    for categoria_id in dados.get("estruturas", {}):
        try:
            bot.add_view(HierarquiaEstruturaView(categoria_id))
            total += 1
        except Exception as erro:
            print(
                f"Erro ao registrar view de estrutura "
                f"{categoria_id}: {erro}"
            )
    bot._views_estruturas_registradas = True
    print(f"Views de estruturas registradas: {total}")


def _role_obrigatorio(guild, role_id, rotulo):
    try:
        role_id = int(role_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"ID inválido em {rotulo}.") from exc
    role = guild.get_role(role_id)
    if role is None:
        raise RuntimeError(
            f"Não encontrei o cargo de {rotulo} ({role_id}) no servidor."
        )
    return role


def _roles_lista(guild, ids, rotulo):
    roles = []
    for role_id in ids:
        role = guild.get_role(int(role_id))
        if role is None:
            raise RuntimeError(
                f"Não encontrei um dos cargos de {rotulo}: {role_id}."
            )
        if role not in roles:
            roles.append(role)
    return roles


def _overwrite_base(*, enviar=None):
    return discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=enviar,
    )


def _montar_overwrites_canal(
    guild,
    departamento,
    supervisores,
    admins,
    enviar_departamento,
    enviar_supervisor,
):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        departamento: _overwrite_base(enviar=enviar_departamento),
    }
    for role in supervisores:
        overwrites[role] = _overwrite_base(enviar=enviar_supervisor)
    for role in admins:
        overwrites[role] = _overwrite_base(enviar=True)
    dono = guild.get_member(DONO_ID)
    if dono is not None:
        overwrites[dono] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            manage_messages=True,
        )
    eu = guild.me
    if eu is not None:
        overwrites[eu] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            manage_messages=True,
        )
    return overwrites


def _montar_overwrites_categoria(
    guild,
    departamento,
    supervisores,
    admins,
):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        departamento: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
        ),
    }
    for role in supervisores:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
        )
    for role in admins:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
        )
    dono = guild.get_member(DONO_ID)
    if dono is not None:
        overwrites[dono] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
        )
    eu = guild.me
    if eu is not None:
        overwrites[eu] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
        )
    return overwrites


async def criar_estrutura_eventos_discord(config):
    guild = obter_guild_painel()
    if guild is None:
        raise RuntimeError(
            "O bot do painel ainda não está conectado ao servidor."
        )
    eu = guild.me
    if (
        eu is None
        or not eu.guild_permissions.manage_channels
        or not eu.guild_permissions.manage_roles
    ):
        raise RuntimeError(
            "O bot precisa das permissões Gerenciar Canais e "
            "Gerenciar Cargos para criar a estrutura e aplicar "
            "as permissões dos canais."
        )

    departamento = _role_obrigatorio(
        guild,
        config["departamento_role_id"],
        "Departamento de Eventos",
    )
    supervisores = _roles_lista(
        guild,
        config.get("supervisor_role_ids", []),
        "Supervisor+",
    )
    admins = _roles_lista(
        guild,
        config.get("admin_role_ids", []),
        "ADM",
    )
    estilo = config.get("estilo", "rm")

    categoria_nome = _nome_com_emoji(
        "📅",
        config.get("categoria_nome") or "Eventos",
        estilo,
    )
    categoria = await guild.create_category(
        categoria_nome,
        overwrites=_montar_overwrites_categoria(
            guild,
            departamento,
            supervisores,
            admins,
        ),
        reason="Resenha Máxima | estrutura criada pelo painel web",
    )

    canais = {}
    especificacoes = [
        (
            "chat",
            "💬",
            config.get("chat_nome") or "chat",
            True,
            True,
        ),
        (
            "anuncios",
            "📢",
            config.get("anuncios_nome") or "anuncios",
            False,
            True,
        ),
        (
            "sugestoes",
            "💡",
            config.get("sugestoes_nome") or "sugestoes",
            True,
            True,
        ),
        (
            "hierarquia",
            "👑",
            config.get("hierarquia_nome") or "hierarquia",
            False,
            False,
        ),
    ]

    try:
        for (
            chave,
            emoji,
            nome_base,
            enviar_dep,
            enviar_sup,
        ) in especificacoes:
            canal = await guild.create_text_channel(
                _nome_com_emoji(
                    emoji,
                    nome_base,
                    estilo,
                ),
                category=categoria,
                overwrites=_montar_overwrites_canal(
                    guild,
                    departamento,
                    supervisores,
                    admins,
                    enviar_dep,
                    enviar_sup,
                ),
                reason=(
                    "Resenha Máxima | estrutura criada pelo painel web"
                ),
            )
            canais[chave] = canal

        await canais["chat"].send(
            "## 💬 Chat do Departamento de Eventos\n"
            "Canal interno para comunicação da equipe."
        )
        await canais["anuncios"].send(
            "## 📢 Anúncios do Departamento de Eventos\n"
            "Os membros podem **ver e ler o histórico**. "
            "Apenas os cargos **Supervisor+** e **ADM** "
            "configurados podem publicar."
        )
        await canais["sugestoes"].send(
            "## 💡 Sugestões\n"
            "Use este canal para ideias, melhorias e propostas "
            "do Departamento de Eventos."
        )

        registro = {
            "categoria_id": str(categoria.id),
            "categoria_nome": categoria.name,
            "departamento_role_id": str(departamento.id),
            "supervisor_role_ids": [
                str(r.id)
                for r in supervisores
            ],
            "admin_role_ids": [
                str(r.id)
                for r in admins
            ],
            "canais": {
                chave: str(canal.id)
                for chave, canal in canais.items()
            },
            "estilo": estilo,
            "criado_em": agora_iso(),
        }
        dados = carregar_estruturas_discord()
        dados.setdefault("estruturas", {})[
            str(categoria.id)
        ] = registro
        salvar_estruturas_discord(dados)

        await canais["hierarquia"].send(
            "## 👑 Hierarquia do Departamento de Eventos\n\n"
            "Este canal é visível para o departamento, com "
            "**Ler histórico de mensagens** habilitado.\n"
            "Somente os **ADMs configurados** podem enviar "
            "mensagens/comandos aqui.\n\n"
            "Use o botão abaixo para consultar o painel da hierarquia.",
            view=HierarquiaEstruturaView(categoria.id),
        )
        return registro
    except Exception:
        try:
            for canal in list(canais.values()):
                try:
                    await canal.delete(
                        reason=(
                            "Rollback: falha ao criar estrutura pelo painel"
                        )
                    )
                except Exception:
                    pass
            await categoria.delete(
                reason="Rollback: falha ao criar estrutura pelo painel"
            )
        except Exception:
            pass
        raise


def _sugestoes_locais_estrutura(tema="eventos"):
    tema_limpo = _sem_acentos(tema).casefold()
    if "evento" in tema_limpo:
        return {
            "categoria": "central de eventos",
            "chat": "chat da equipe",
            "anuncios": "comunicados",
            "sugestoes": "sugestoes",
            "hierarquia": "hierarquia",
        }
    return {
        "categoria": tema_limpo or "departamento",
        "chat": "chat da equipe",
        "anuncios": "anuncios",
        "sugestoes": "sugestoes",
        "hierarquia": "hierarquia",
    }


def sugerir_nomes_estrutura_ia(tema):
    fallback = _sugestoes_locais_estrutura(tema)
    chave = os.getenv("GROQ_API_KEY", "").strip()
    if not chave:
        return (
            fallback,
            "preset local (GROQ_API_KEY não configurada no SITE)",
        )

    prompt = (
        "Você cria nomes curtos de canais para um servidor Discord "
        "brasileiro chamado Resenha Máxima. Retorne SOMENTE JSON "
        "válido, sem markdown, com as chaves categoria, chat, anuncios, "
        "sugestoes, hierarquia. Os nomes devem ser curtos, administrativos "
        "e combinar entre si. Não use emojis nem fontes unicode; o painel "
        "aplicará a fonte depois. Tema: "
        + str(tema or "Departamento de Eventos")
    )
    payload = json.dumps(
        {
            "model": os.getenv(
                "GROQ_SITE_MODEL",
                "llama-3.1-8b-instant",
            ),
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": 0.8,
            "max_completion_tokens": 220,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {chave}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req,
            timeout=12,
        ) as resposta:
            bruto = json.loads(
                resposta.read().decode("utf-8")
            )
        conteudo = bruto[
            "choices"
        ][0]["message"]["content"].strip()
        match = re.search(r"\{.*\}", conteudo, re.S)
        if match:
            dados = json.loads(match.group(0))
            saida = {}
            for chave_nome in (
                "categoria",
                "chat",
                "anuncios",
                "sugestoes",
                "hierarquia",
            ):
                valor = str(
                    dados.get(chave_nome)
                    or fallback[chave_nome]
                ).strip()[:50]
                saida[chave_nome] = valor
            return saida, "IA Groq"
    except Exception as erro:
        print(
            "Sugestão de nomes por IA indisponível: "
            f"{erro!r}"
        )

    return fallback, "preset local (fallback da IA)"


ESTRUTURAS_HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Resenha Máxima • Construtor de Categorias</title>
  <style>
    :root{color-scheme:dark;--bg:#101114;--card:#181a1f;--card2:#20232a;--text:#f4f4f5;--muted:#a4a7ae;--gold:#d4af37;--line:#30343d}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,system-ui,Segoe UI,sans-serif}a{color:inherit}.top{position:sticky;top:0;z-index:5;background:#121318;border-bottom:1px solid var(--line);padding:14px 24px;display:flex;justify-content:space-between;align-items:center}.brand{font-weight:900;letter-spacing:.06em}.brand span{color:var(--gold)}.wrap{max-width:1180px;margin:28px auto;padding:0 18px 60px}.hero{padding:24px;border:1px solid #4a4020;background:linear-gradient(135deg,#1d1a10,#181a1f);border-radius:18px;margin-bottom:20px}.hero h1{margin:4px 0 8px;font-size:28px}.hero p{color:var(--muted);margin:0}.tag{display:inline-block;color:var(--gold);font-weight:800;font-size:12px;letter-spacing:.08em}.grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(320px,.75fr);gap:18px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:18px}.card h2{margin:0 0 6px}.muted{color:var(--muted)}label{display:block;font-weight:700;margin:14px 0 6px}input,select{width:100%;background:#111318;border:1px solid #383d47;color:var(--text);border-radius:10px;padding:11px 12px;outline:none}input:focus,select:focus{border-color:var(--gold)}.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}button,.btn{border:0;border-radius:10px;padding:11px 15px;font-weight:800;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:7px}.primary{background:var(--gold);color:#17130a}.secondary{background:#2b2f37;color:var(--text);border:1px solid #414650}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}.notice{padding:12px 14px;border-radius:10px;margin:12px 0;background:#20251e;border:1px solid #3e5d40}.preview{background:#111318;border:1px solid var(--line);padding:15px;border-radius:13px}.preview .cat{font-weight:900;color:var(--gold);margin-bottom:9px}.channel{padding:7px 9px;border-radius:8px;background:#1d2026;margin:6px 0}.perm{font-size:13px;color:var(--muted);margin-top:10px}.history{display:grid;gap:10px}.item{background:#111318;border:1px solid var(--line);border-radius:12px;padding:13px}.item strong{color:var(--gold)}.status{font-size:13px;margin-top:8px;color:var(--muted)}@media(max-width:820px){.grid{grid-template-columns:1fr}.two{grid-template-columns:1fr}.top{padding:12px 15px}.wrap{margin-top:18px}}
  </style>
</head>
<body>
  <div class="top">
    <div class="brand">RESENHA <span>MÁXIMA</span> • Estruturas</div>
    <div><a class="btn secondary" href="{{ url_for('painel') }}">← Voltar ao painel</a></div>
  </div>
  <main class="wrap">
    <section class="hero">
      <span class="tag">CONSTRUTOR DE CATEGORIAS</span>
      <h1>🏗️ Estruturas do Discord</h1>
      <p>Crie uma categoria completa com canais, fontes e permissões específicas sem configurar tudo manualmente.</p>
    </section>
    {% with messages = get_flashed_messages() %}
      {% for message in messages %}<div class="notice">{{ message }}</div>{% endfor %}
    {% endwith %}
    <div class="grid">
      <div>
        <form class="card" method="post" action="{{ url_for('criar_estrutura_eventos') }}" id="estrutura-form">
          <h2>📅 Categoria de Eventos</h2>
          <p class="muted">Cria chat, anúncios, sugestões e hierarquia. Todo cargo que pode visualizar recebe automaticamente <b>Ver canal + Ler histórico de mensagens</b>.</p>
          <div class="two">
            <div>
              <label>Nome/tema da categoria</label>
              <input id="categoria_nome" name="categoria_nome" value="central de eventos" maxlength="50" required>
            </div>
            <div>
              <label>Estilo da fonte</label>
              <select id="estilo" name="estilo">
                {% for chave, nome in estilos.items() %}
                  <option value="{{ chave }}" {% if chave == 'rm' %}selected{% endif %}>{{ nome }}</option>
                {% endfor %}
              </select>
            </div>
          </div>
          <label>ID do cargo Departamento de Eventos</label>
          <input name="departamento_role_id" placeholder="Ex.: 154..." inputmode="numeric" required>
          <label>IDs dos cargos Supervisor+ que podem publicar anúncios</label>
          <input name="supervisor_role_ids" placeholder="Separe por vírgula: 154..., 154...">
          <label>IDs dos cargos ADM que podem escrever na hierarquia</label>
          <input name="admin_role_ids" placeholder="Separe por vírgula: 154..., 154...">
          <div class="card" style="padding:14px;margin-top:18px;background:var(--card2)">
            <b>🔐 Regra automática de histórico</b>
            <div class="muted">Se um cargo puder ver qualquer canal criado aqui, <b>Ler histórico de mensagens</b> será habilitado junto. Essa permissão não depende do campo de escrita.</div>
          </div>
          <h3>Nomes dos canais</h3>
          <div class="two">
            <div><label>Chat</label><input id="chat_nome" name="chat_nome" value="chat da equipe" required></div>
            <div><label>Anúncios</label><input id="anuncios_nome" name="anuncios_nome" value="comunicados" required></div>
          </div>
          <div class="two">
            <div><label>Sugestões</label><input id="sugestoes_nome" name="sugestoes_nome" value="sugestoes" required></div>
            <div><label>Hierarquia</label><input id="hierarquia_nome" name="hierarquia_nome" value="hierarquia" required></div>
          </div>
          <div class="actions">
            <button class="secondary" type="button" id="ia-nomes">✨ Sugerir nomes com IA</button>
            <button class="primary" type="submit" onclick="return confirm('Criar esta categoria e os 4 canais no Discord?')">🚀 Criar no Discord</button>
          </div>
          <div id="ia-status" class="status"></div>
        </form>
      </div>
      <aside>
        <section class="card">
          <h2>👁️ Prévia</h2>
          <div class="preview">
            <div class="cat" id="prev-cat">📅・CENTRAL-DE-EVENTOS</div>
            <div class="channel" id="prev-chat">💬・CHAT-DA-EQUIPE</div>
            <div class="channel" id="prev-anuncios">📢・COMUNICADOS</div>
            <div class="channel" id="prev-sugestoes">💡・SUGESTOES</div>
            <div class="channel" id="prev-hierarquia">👑・HIERARQUIA</div>
            <div class="perm"><b>Anúncios:</b> Departamento vê + histórico; Supervisor+/ADM escreve.<br><b>Hierarquia:</b> Departamento vê + histórico; somente ADM escreve.</div>
          </div>
        </section>
        <section class="card">
          <h2>📚 Estruturas criadas</h2>
          <div class="history">
            {% if estruturas %}
              {% for item in estruturas %}
                <div class="item">
                  <strong>{{ item.categoria_nome }}</strong><br>
                  <span class="muted">Categoria: {{ item.categoria_id }}</span><br>
                  <span class="muted">Criada: {{ item.criado_em }}</span>
                </div>
              {% endfor %}
            {% else %}
              <div class="muted">Nenhuma estrutura criada pelo painel ainda.</div>
            {% endif %}
          </div>
        </section>
      </aside>
    </div>
  </main>
<script>
(() => {
  const qs = id => document.getElementById(id);
  const clean = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim().replace(/\s+/g, '-').toUpperCase();
  function preview(){
    qs('prev-cat').textContent='📅・'+clean(qs('categoria_nome').value);
    qs('prev-chat').textContent='💬・'+clean(qs('chat_nome').value);
    qs('prev-anuncios').textContent='📢・'+clean(qs('anuncios_nome').value);
    qs('prev-sugestoes').textContent='💡・'+clean(qs('sugestoes_nome').value);
    qs('prev-hierarquia').textContent='👑・'+clean(qs('hierarquia_nome').value);
  }
  ['categoria_nome','chat_nome','anuncios_nome','sugestoes_nome','hierarquia_nome','estilo'].forEach(id => qs(id).addEventListener('input', preview));
  preview();
  qs('ia-nomes').addEventListener('click', async () => {
    const st = qs('ia-status');
    st.textContent = 'Gerando sugestões...';
    try {
      const r = await fetch('{{ url_for("sugerir_nomes_estrutura") }}', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({tema:qs('categoria_nome').value})
      });
      const d = await r.json();
      if(!r.ok) throw new Error(d.erro || 'Falha');
      const n = d.nomes;
      qs('categoria_nome').value=n.categoria;
      qs('chat_nome').value=n.chat;
      qs('anuncios_nome').value=n.anuncios;
      qs('sugestoes_nome').value=n.sugestoes;
      qs('hierarquia_nome').value=n.hierarquia;
      preview();
      st.textContent='✅ Sugestões: '+d.fonte;
    } catch(e) {
      st.textContent='❌ '+e.message;
    }
  });
})();
</script>
</body>
</html>"""


# =========================================================
# SITE / PAINEL WEB
# =========================================================

app = Flask(__name__)
app.secret_key = os.getenv("PANEL_SECRET_KEY") or secrets.token_hex(32)

USERS_FILE = DATA_DIR / "panel_users.json"
ADMIN_LOG_FILE = DATA_DIR / "admin_logs.json"
ENTRADAS_FILE = DATA_DIR / "entradas.json"

# Atualizações publicadas pelo site.
# Fica no Volume (/data) para sobreviver a deploys/reinícios.
ATUALIZACOES_FILE = DATA_DIR / "atualizacoes_painel.json"
_atualizacoes_lock = threading.Lock()

IA_CONFIG_FILE = DATA_DIR / "ia_config.json"
_ia_config_lock = threading.Lock()

# =========================================================
# RECRUTAMENTO — DEPARTAMENTO DE EVENTOS / GOOGLE FORMS
# =========================================================

EVENTOS_RECRUTAMENTO_FILE = DATA_DIR / "recrutamento_eventos.json"
_eventos_recrutamento_lock = threading.Lock()

# Estruturas/categorias criadas pelo painel web.
ESTRUTURAS_DISCORD_FILE = DATA_DIR / "estruturas_discord.json"
_estruturas_discord_lock = threading.Lock()

EVENTOS_RECRUTAMENTO_SECRET = os.getenv("EVENTOS_RECRUTAMENTO_SECRET", "").strip()
EVENTOS_FORMS_URL = "https://forms.gle/h4kt2Cp7fduGG4Pc8"
EVENTOS_REFAZER_HORAS = 24
CONTA_TESTE_ID = 1532838576256057557


def recrutamento_eventos_vazio():
    return {
        "versao": 2,
        "candidaturas": {},
        "cooldowns": {},
        "config": {
            "prefill_script_url": "",
        },
    }


def _salvar_recrutamento_eventos_sem_lock(dados):
    temporario = EVENTOS_RECRUTAMENTO_FILE.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
    temporario.replace(EVENTOS_RECRUTAMENTO_FILE)


def carregar_recrutamento_eventos():
    with _eventos_recrutamento_lock:
        if not EVENTOS_RECRUTAMENTO_FILE.exists():
            _salvar_recrutamento_eventos_sem_lock(recrutamento_eventos_vazio())

        try:
            with EVENTOS_RECRUTAMENTO_FILE.open("r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (OSError, json.JSONDecodeError):
            dados = recrutamento_eventos_vazio()

        if not isinstance(dados, dict):
            dados = recrutamento_eventos_vazio()
        if not isinstance(dados.get("candidaturas"), dict):
            dados["candidaturas"] = {}
        if not isinstance(dados.get("cooldowns"), dict):
            dados["cooldowns"] = {}
        if not isinstance(dados.get("config"), dict):
            dados["config"] = {}
        dados["config"].setdefault("prefill_script_url", "")
        dados.setdefault("versao", 2)
        return dados


def salvar_recrutamento_eventos(dados):
    with _eventos_recrutamento_lock:
        _salvar_recrutamento_eventos_sem_lock(dados)


def _agora_utc_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_utc(valor):
    try:
        dt = datetime.fromisoformat(str(valor or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _segundos_restantes_cooldown(valor):
    fim = _parse_iso_utc(valor)
    if fim is None:
        return 0
    return max(0, int((fim - datetime.now(timezone.utc)).total_seconds()))


def _gerar_codigo_candidatura(dados):
    for _ in range(30):
        codigo = "RM-EVT-" + secrets.token_hex(3).upper()
        if codigo not in dados.get("candidaturas", {}):
            return codigo
    raise RuntimeError("Não foi possível gerar um código de candidatura único.")


def _autorizado_recrutamento_eventos(payload=None):
    segredo = request.headers.get("X-Eventos-Secret", "").strip()
    if not segredo and isinstance(payload, dict):
        segredo = str(payload.get("secret") or "").strip()
    return bool(
        EVENTOS_RECRUTAMENTO_SECRET
        and segredo
        and secrets.compare_digest(segredo, EVENTOS_RECRUTAMENTO_SECRET)
    )


def _candidatura_por_canal(dados, canal_id):
    canal_id = str(canal_id or "").strip()
    for candidatura in dados.get("candidaturas", {}).values():
        if str(candidatura.get("discord_channel_id") or "") == canal_id:
            return candidatura
    return None


# =========================================================
# IDENTIDADE GAMER — ROBLOX OAUTH 2.0 / OPENID CONNECT
# =========================================================

ROBLOX_VINCULOS_FILE = DATA_DIR / "roblox_vinculos.json"
_roblox_vinculos_lock = threading.Lock()

ROBLOX_VINCULO_SECRET = os.getenv(
    "ROBLOX_VINCULO_SECRET",
    "",
).strip()
ROBLOX_CLIENT_ID = os.getenv(
    "ROBLOX_CLIENT_ID",
    "",
).strip()
ROBLOX_CLIENT_SECRET = os.getenv(
    "ROBLOX_CLIENT_SECRET",
    "",
).strip()
ROBLOX_REDIRECT_URI = os.getenv(
    "ROBLOX_REDIRECT_URI",
    "https://resenha-maxima.up.railway.app/roblox/callback",
).strip()

ROBLOX_AUTHORIZE_URL = (
    "https://apis.roblox.com/oauth/v1/authorize"
)
ROBLOX_TOKEN_URL = (
    "https://apis.roblox.com/oauth/v1/token"
)
ROBLOX_USERINFO_URL = (
    "https://apis.roblox.com/oauth/v1/userinfo"
)
ROBLOX_PENDENCIA_MINUTOS = 15


def roblox_vinculos_vazio():
    return {
        "versao": 1,
        "vinculos": {},
        "pendentes": {},
    }


def _salvar_roblox_vinculos_sem_lock(dados):
    temporario = ROBLOX_VINCULOS_FILE.with_suffix(".tmp")
    with temporario.open(
        "w",
        encoding="utf-8",
    ) as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2,
        )
    temporario.replace(ROBLOX_VINCULOS_FILE)


def carregar_roblox_vinculos():
    with _roblox_vinculos_lock:
        if not ROBLOX_VINCULOS_FILE.exists():
            _salvar_roblox_vinculos_sem_lock(
                roblox_vinculos_vazio()
            )

        try:
            with ROBLOX_VINCULOS_FILE.open(
                "r",
                encoding="utf-8",
            ) as arquivo:
                dados = json.load(arquivo)
        except (
            OSError,
            json.JSONDecodeError,
        ):
            dados = roblox_vinculos_vazio()

        if not isinstance(dados, dict):
            dados = roblox_vinculos_vazio()
        if not isinstance(dados.get("vinculos"), dict):
            dados["vinculos"] = {}
        if not isinstance(dados.get("pendentes"), dict):
            dados["pendentes"] = {}
        dados.setdefault("versao", 1)

        agora = datetime.now(timezone.utc)
        expirados = []
        for token, item in dados["pendentes"].items():
            expira = _parse_iso_utc(
                item.get("expira_em")
                if isinstance(item, dict)
                else None
            )
            if expira is None or expira <= agora:
                expirados.append(token)
        if expirados:
            for token in expirados:
                dados["pendentes"].pop(token, None)
            _salvar_roblox_vinculos_sem_lock(dados)

        return dados


def salvar_roblox_vinculos(dados):
    with _roblox_vinculos_lock:
        _salvar_roblox_vinculos_sem_lock(dados)


def _autorizado_roblox(payload=None):
    segredo = request.headers.get(
        "X-Roblox-Link-Secret",
        "",
    ).strip()
    if not segredo and isinstance(payload, dict):
        segredo = str(
            payload.get("secret")
            or ""
        ).strip()

    return bool(
        ROBLOX_VINCULO_SECRET
        and segredo
        and secrets.compare_digest(
            segredo,
            ROBLOX_VINCULO_SECRET,
        )
    )


def _roblox_configurado():
    return bool(
        ROBLOX_CLIENT_ID
        and ROBLOX_CLIENT_SECRET
        and ROBLOX_REDIRECT_URI
    )


def _pagina_roblox(
    titulo,
    mensagem,
    sucesso=False,
):
    cor = "#57F287" if sucesso else "#ED4245"
    icone = "✅" if sucesso else "⚠️"
    titulo_seguro = str(titulo).replace(
        "<", "&lt;"
    ).replace(">", "&gt;")
    mensagem_segura = str(mensagem).replace(
        "<", "&lt;"
    ).replace(">", "&gt;")
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo_seguro} • Resenha Máxima</title>
<style>
body {{
  margin:0; min-height:100vh; display:grid; place-items:center;
  background:#111214; color:#fff; font-family:Arial,sans-serif;
}}
.card {{
  width:min(560px,calc(100% - 40px)); background:#1e1f22;
  border:1px solid #2b2d31; border-radius:18px; padding:30px;
  box-shadow:0 18px 60px rgba(0,0,0,.35);
}}
h1 {{ margin:0 0 14px; font-size:24px; }}
p {{ color:#dbdee1; line-height:1.55; white-space:pre-line; }}
.badge {{ color:{cor}; font-weight:700; }}
small {{ color:#949ba4; }}
</style>
</head>
<body>
  <main class="card">
    <div class="badge">{icone} RESENHA MÁXIMA</div>
    <h1>{titulo_seguro}</h1>
    <p>{mensagem_segura}</p>
    <small>Você já pode voltar para o Discord.</small>
  </main>
</body>
</html>"""


def _post_form_roblox(url, campos):
    corpo = urllib.parse.urlencode(
        campos
    ).encode("utf-8")
    requisicao = urllib.request.Request(
        url,
        data=corpo,
        headers={
            "Content-Type": (
                "application/x-www-form-urlencoded"
            ),
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(
        requisicao,
        timeout=12,
    ) as resposta:
        bruto = resposta.read().decode(
            "utf-8",
            errors="replace",
        )
    return json.loads(bruto)


def _userinfo_roblox(access_token):
    requisicao = urllib.request.Request(
        ROBLOX_USERINFO_URL,
        headers={
            "Authorization": (
                f"Bearer {access_token}"
            ),
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(
        requisicao,
        timeout=12,
    ) as resposta:
        bruto = resposta.read().decode(
            "utf-8",
            errors="replace",
        )
    return json.loads(bruto)


IA_CONFIG_PADRAO = {
    "ativa": True,
    "canal_id": "",
    "caos_ativo": True,
    "caos_hora_inicio": 6,
    "caos_hora_fim": 23,
    "caos_intervalo_minutos": 120,
    "caos_chance": 0.12,
    "call_cooldown_minutos": 10,
}


# O Discord aceita até 2000 caracteres por mensagem.
# Usamos margem para evitar falhas com formatação/menções.
DISCORD_MESSAGE_SAFE_LIMIT = 1900

# Cargos reconhecidos pelo painel
CARGO_BANIMENTOS_ID = 1536734408277491863
CARGO_ENTRADA_ID = 1536894123657658468
CARGO_PATENTE_MINECRAFT_ID = 1537595632817012846

_entradas_lock = threading.Lock()
_invites_cache = {}
_vanity_cache = {}

# Equipe de Desenvolvimento já definida acima:
# CARGO_DESENVOLVIMENTO_ID = 1533625836874498181
#
# Para o Departamento de Eventos e canal de Eventos, você pode
# definir os IDs diretamente na Railway. Se não definir, o painel
# tenta localizar pelo nome no Discord.
CARGO_EVENTOS_ID = int(os.getenv("CARGO_EVENTOS_ID", "0") or "0")
CANAL_EVENTOS_ID = int(os.getenv("CANAL_EVENTOS_ID", "1535124939940823110") or "1535124939940823110")

_users_lock = threading.Lock()


# =========================================================
# CONFIGURAÇÃO DA IA
# =========================================================

def carregar_config_ia():
    with _ia_config_lock:
        dados = dict(IA_CONFIG_PADRAO)

        if IA_CONFIG_FILE.exists():
            try:
                with IA_CONFIG_FILE.open("r", encoding="utf-8") as arquivo:
                    salvo = json.load(arquivo)
                if isinstance(salvo, dict):
                    dados.update(salvo)
            except (OSError, json.JSONDecodeError):
                pass

        return dados


def salvar_config_ia(dados):
    with _ia_config_lock:
        atual = dict(IA_CONFIG_PADRAO)
        if isinstance(dados, dict):
            atual.update(dados)

        temporario = IA_CONFIG_FILE.with_suffix(".tmp")
        with temporario.open("w", encoding="utf-8") as arquivo:
            json.dump(
                atual,
                arquivo,
                ensure_ascii=False,
                indent=2
            )
        temporario.replace(IA_CONFIG_FILE)


def _bool_form(nome):
    return request.form.get(nome) in {"1", "true", "on", "yes"}


def _int_form(nome, padrao, minimo, maximo):
    try:
        valor = int(request.form.get(nome, padrao))
    except (TypeError, ValueError):
        valor = padrao
    return max(minimo, min(maximo, valor))


def _float_form(nome, padrao, minimo, maximo):
    try:
        valor = float(
            str(request.form.get(nome, padrao)).replace(",", ".")
        )
    except (TypeError, ValueError):
        valor = padrao
    return max(minimo, min(maximo, valor))


# =========================================================
# ATUALIZAÇÕES / ROADMAP DO BOT
# =========================================================

# Rodapé aleatório das "Futuras atualizações".
# Mantém o título fixo e varia apenas a frase que aparece logo abaixo.
FUTURAS_FRASES_FINAIS = [
    "Não sabemos, tem que ver com o ADM-G aí.",
    "Pergunta pro Baiano do Vini, eu só trabalho aqui.",
    "Sem previsão. O Vini ainda tá inventando moda.",
    "Quando esse corno que me programa criar coragem.",
    "Algum dia... provavelmente.",
    "Assim que o ADM-G parar de adicionar coisa nova.",
    "O calendário do Baiano do Vini ainda não chegou aqui.",
    "Em breve™. Reclama com o programador.",
    "Data? Só o Vini e Deus sabem.",
    "Estamos aguardando o ADM-G decidir.",
    "O vagabundo do Vini disse que só quando o PT sair do governo.",
    "Provavelmente o ADM-G tá esperando o Hexa antes da atualização.",
    "Esse corno que me programa deve estar esperando o GTA VI sair primeiro.",
    "O Baiano do Vini falou ‘em breve’. Traduzindo: pode sentar e esperar.",
    "Segundo o Vini, falta pouco. Ele também fala isso faz tempo.",
    "Talvez saia antes do próximo feriado. Talvez.",
    "O Baiano tá trabalhando nisso. Fonte: vozes da minha cabeça.",
    "Quando o ADM-G parar de adicionar ideia nova antes de terminar a antiga.",
    "Atualização prevista para quando Deus quiser e o Vini colaborar.",
    "O programador jurou que está quase pronto. Eu também queria acreditar.",
    "Vai cobrar o ADM-G, não vem descontar em mim não.",
    "Meu querido programador desocupado disse ‘já já’. Faça sua própria interpretação.",
    "O Vini prometeu terminar antes do Brasil ganhar outro Mundial. Estamos preocupados.",
    "O Baiano do Vini deve estar compilando a atualização em uma calculadora.",
]


# Stickers já usados anteriormente pelo painel da Resenha Máxima.
FUTURAS_STICKER_IDS = [
    1534435954557845724,  # Sonic — "vish kkk"
    1532830961283371179,  # "isso foi uma ameaça?"
    1534435078413746306,  # boneca planejando algo
    1534440284358705222,  # comemoração/troféu
    1534440607001477198,  # "Posso ser admin?"
]


FUTURAS_STICKERS_CONTEXTO = {
    "demora": [1534435954557845724, 1532830961283371179],
    "planejando": [1534435078413746306, 1534435954557845724],
    "grande": [1534440284358705222, 1534435078413746306],
    "admin": [1534440607001477198, 1534435954557845724],
}



def escolher_rodape_futuras(anteriores=None):
    anteriores = anteriores or {}
    frase_anterior = str(anteriores.get("frase_final") or "")
    sticker_anterior = str(anteriores.get("sticker_id") or "")

    frases = [
        frase for frase in FUTURAS_FRASES_FINAIS
        if frase != frase_anterior
    ] or FUTURAS_FRASES_FINAIS
    frase = random.choice(frases)

    normalizada = frase.casefold()
    if any(x in normalizada for x in ("hexa", "gta", "feriado", "em breve", "esperando", "demora", "algum dia")):
        grupo = "demora"
    elif any(x in normalizada for x in ("inventando", "adicionar", "trabalhando", "compilando", "programador")):
        grupo = "planejando"
    elif any(x in normalizada for x in ("adm", "admin")):
        grupo = "admin"
    else:
        grupo = "grande"

    candidatos = [str(x) for x in FUTURAS_STICKERS_CONTEXTO.get(grupo, FUTURAS_STICKER_IDS)]
    candidatos = [x for x in candidatos if x != sticker_anterior] or candidatos
    return frase, random.choice(candidatos)



def _remover_titulo_futuras_existente(texto):
    linhas = str(texto or "").strip().splitlines()
    if not linhas:
        return ""

    primeira = linhas[0].strip()
    normalizada = primeira.casefold()
    normalizada = (
        normalizada
        .replace("á", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("à", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )

    titulo_limpo = normalizada.lstrip("# ").strip().lstrip("🔮 ").strip()
    if (
        titulo_limpo.startswith("futuras atualizacoes")
        or titulo_limpo.startswith("proximas atualizacoes")
    ):
        linhas = linhas[1:]

    return "\n".join(linhas).strip()


def montar_texto_futuras(texto, frase_final):
    base = _remover_titulo_futuras_existente(texto)
    partes = ["# 🔮 Futuras atualizações"]
    if base:
        partes.append(base)
    partes.append(
        "**📅 Data da atualização:**\n"
        f"{frase_final}"
    )
    return "\n\n".join(partes)


def montar_texto_notas(texto):
    """Garante um único título fixo para notas lançadas pelo painel."""
    linhas = str(texto or "").strip().splitlines()

    while linhas and not linhas[0].strip():
        linhas.pop(0)

    if linhas:
        primeira = linhas[0].strip()
        normalizada = primeira.casefold()
        normalizada = (
            normalizada
            .replace("á", "a")
            .replace("ã", "a")
            .replace("â", "a")
            .replace("à", "a")
            .replace("é", "e")
            .replace("ê", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ô", "o")
            .replace("õ", "o")
            .replace("ú", "u")
            .replace("ç", "c")
        )

        # Remove apenas um título geral antigo. Se a primeira linha já for
        # uma seção como "## 🆕 NOVIDADES", ela é preservada.
        titulo_geral = normalizada.lstrip("# ").strip().lstrip("📝 ").strip()
        if (
            "nota" in titulo_geral
            or titulo_geral.startswith("atualizacao")
        ) and not any(
            palavra in titulo_geral
            for palavra in ("novidades", "correcoes", "alteracoes", "problemas")
        ):
            linhas = linhas[1:]

    corpo = "\n".join(linhas).strip()
    if corpo:
        return "# Notas de atualização\n\n" + corpo
    return "# Notas de atualização"


def atualizacoes_vazias():
    return {
        "versao": 1,
        "canal_id": "",
        "futuras": {
            "texto": "",
            "mensagens_ids": [],
            "canal_id": "",
            "publicado_em": "",
            "frase_final": "",
            "sticker_id": "",
        },
        "historico": [],
    }


def salvar_atualizacoes_sem_lock(dados):
    temporario = ATUALIZACOES_FILE.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2
        )
    temporario.replace(ATUALIZACOES_FILE)


def carregar_atualizacoes():
    with _atualizacoes_lock:
        if not ATUALIZACOES_FILE.exists():
            salvar_atualizacoes_sem_lock(
                atualizacoes_vazias()
            )

        try:
            with ATUALIZACOES_FILE.open(
                "r",
                encoding="utf-8"
            ) as arquivo:
                dados = json.load(arquivo)
        except (OSError, json.JSONDecodeError):
            dados = atualizacoes_vazias()
            salvar_atualizacoes_sem_lock(dados)

        if not isinstance(dados, dict):
            dados = atualizacoes_vazias()

        dados.setdefault("versao", 1)
        dados.setdefault("canal_id", "")
        dados.setdefault(
            "futuras",
            atualizacoes_vazias()["futuras"]
        )
        dados.setdefault("historico", [])

        if not isinstance(dados["historico"], list):
            dados["historico"] = []

        if not isinstance(dados["futuras"], dict):
            dados["futuras"] = atualizacoes_vazias()["futuras"]

        dados["futuras"].setdefault("frase_final", "")
        dados["futuras"].setdefault("sticker_id", "")

        return dados


def salvar_atualizacoes(dados):
    with _atualizacoes_lock:
        salvar_atualizacoes_sem_lock(dados)


def quebrar_mensagem_discord(
    texto,
    limite=DISCORD_MESSAGE_SAFE_LIMIT
):
    """
    Divide texto longo preservando parágrafos sempre que possível.
    As partes são mensagens normais do Discord, nunca embeds.
    """
    texto = str(texto or "").replace("\\r\\n", "\\n").strip()

    if not texto:
        return []

    if len(texto) <= limite:
        return [texto]

    partes = []
    restante = texto

    while restante:
        if len(restante) <= limite:
            partes.append(restante.strip())
            break

        corte = restante.rfind("\\n\\n", 0, limite + 1)

        if corte < max(200, limite // 3):
            corte = restante.rfind("\\n", 0, limite + 1)

        if corte < max(200, limite // 3):
            corte = restante.rfind(" ", 0, limite + 1)

        if corte <= 0:
            corte = limite

        parte = restante[:corte].strip()

        if parte:
            partes.append(parte)

        restante = restante[corte:].lstrip()

    return partes


async def localizar_canal_atualizacoes(
    canal_id=None
):
    dados = carregar_atualizacoes()

    escolhido = str(
        canal_id
        or dados.get("canal_id")
        or ""
    ).strip()

    if escolhido:
        try:
            return await localizar_canal(
                escolhido
            )
        except RuntimeError:
            pass

    guild = obter_guild_painel()

    if guild is None:
        raise RuntimeError(
            "O bot do painel ainda não está conectado ao servidor."
        )

    # Fallback para facilitar a primeira configuração.
    # Procura o canal que já está sendo usado para atualizações.
    for canal in guild.text_channels:
        nome = (
            canal.name
            .casefold()
            .replace("_", "-")
        )

        if "atualiza" in nome:
            return canal

    raise RuntimeError(
        "Canal de atualizações não configurado. "
        "Escolha o canal na aba Atualizações."
    )


async def enviar_texto_normal_discord(
    canal,
    texto
):
    ids = []

    for parte in quebrar_mensagem_discord(
        texto
    ):
        mensagem = await canal.send(
            parte,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False
            )
        )
        ids.append(
            str(mensagem.id)
        )

    return ids


async def enviar_sticker_futuras(canal, sticker_id):
    """Envia um sticker do servidor e devolve o ID da mensagem criada."""
    try:
        sticker = await canal.guild.fetch_sticker(
            int(sticker_id)
        )
        mensagem = await canal.send(
            stickers=[sticker]
        )
        return str(mensagem.id)
    except (
        ValueError,
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException
    ) as erro:
        # Se um sticker tiver sido removido ou o bot não puder usá-lo,
        # a mensagem textual de futuras atualizações continua publicada.
        print(
            "Aviso: não foi possível enviar sticker "
            f"{sticker_id}: {erro!r}",
            flush=True
        )
        return ""


async def apagar_mensagens_por_ids(
    canal,
    mensagens_ids
):
    removidas = 0

    for mensagem_id in mensagens_ids or []:
        try:
            mensagem = await canal.fetch_message(
                int(mensagem_id)
            )
            await mensagem.delete()
            removidas += 1
        except (
            ValueError,
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            continue

    return removidas


async def apagar_futuras_por_varredura(canal, limite=300):
    """Fallback: remove prévias antigas mesmo se o ID salvo no site tiver se perdido."""
    try:
        mensagens = [m async for m in canal.history(limit=limite)]
    except (discord.Forbidden, discord.HTTPException):
        return 0

    bot_user = getattr(bot, "user", None)
    if bot_user is None:
        return 0

    ids = set()
    futuros = []
    for msg in mensagens:
        if msg.author.id != bot_user.id:
            continue
        primeira = str(msg.content or "").strip().splitlines()
        primeira = primeira[0] if primeira else ""
        norm = primeira.casefold().lstrip("# ").strip().lstrip("🔮 ").strip()
        if norm.startswith("futuras atualizações") or norm.startswith("futuras atualizacoes") or norm.startswith("próximas atualizações") or norm.startswith("proximas atualizacoes"):
            futuros.append(msg)
            ids.add(msg.id)

    # Sticker do rodapé: remove apenas quando foi enviado pelo bot logo após uma prévia detectada.
    stickers_permitidos = {int(x) for x in FUTURAS_STICKER_IDS}
    for previa in futuros:
        for msg in mensagens:
            if msg.author.id != bot_user.id or not msg.stickers:
                continue
            delta = (msg.created_at - previa.created_at).total_seconds()
            if 0 <= delta <= 60 and any(int(s.id) in stickers_permitidos for s in msg.stickers):
                ids.add(msg.id)

    removidas = 0
    for msg in mensagens:
        if msg.id not in ids:
            continue
        try:
            await msg.delete(reason="Limpeza de Futuras Atualizações duplicadas")
            removidas += 1
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
    return removidas


def executar_no_bot(
    coroutine,
    timeout=20
):
    if not TOKEN:
        raise RuntimeError(
            "TOKEN não configurado no Railway."
        )

    if not bot.is_ready() or BOT_LOOP is None:
        raise RuntimeError(
            "O bot do painel ainda não está conectado ao Discord."
        )

    futuro = asyncio.run_coroutine_threadsafe(
        coroutine,
        BOT_LOOP
    )

    return futuro.result(
        timeout=timeout
    )


def resumo_atualizacoes_painel():
    dados = carregar_atualizacoes()
    futuras = dados.get("futuras") or {}

    return {
        "canal_id": str(
            dados.get("canal_id")
            or ""
        ),
        "futuras_texto": str(
            futuras.get("texto")
            or ""
        ),
        "futuras_publicadas": bool(
            futuras.get("mensagens_ids")
        ),
        "futuras_publicado_em": str(
            futuras.get("publicado_em")
            or ""
        ),
        "historico": list(
            reversed(
                dados.get("historico", [])[-20:]
            )
        ),
    }


MODELOS_MENSAGENS = [
    {
        "categoria": "Minecraft",
        "titulo": "Solicitação de nickname",
        "destino": "DM do membro",
        "conteudo": (
            "🎮 Cadastro do Minecraft\n\n"
            "Precisamos do seu nickname do Minecraft para concluir seu cadastro.\n"
            "Responda esta mensagem com o nickname que você usa no servidor."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "Nickname pendente",
        "destino": "DM do membro",
        "conteudo": (
            "⚠️ Seu nickname do Minecraft ainda está pendente.\n\n"
            "Envie seu nickname para concluir o cadastro. "
            "O sistema pode enviar até 4 avisos dentro de 48 horas."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "DM fechada",
        "destino": "Canal do servidor",
        "conteudo": (
            "📩 Não consegui enviar uma mensagem no seu privado.\n"
            "Abra suas DMs para que o cadastro do nickname possa continuar."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "Nickname cadastrado",
        "destino": "DM do membro",
        "conteudo": (
            "✅ Nickname cadastrado com sucesso.\n\n"
            "Seu nickname foi adicionado à tabela do Minecraft."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "Status Online",
        "destino": "Canal de status",
        "conteudo": (
            "🟢 SERVIDOR MINECRAFT ONLINE\n\n"
            "O servidor da Resenha Máxima está disponível agora."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "Status Offline",
        "destino": "Canal de status",
        "conteudo": (
            "🔴 SERVIDOR MINECRAFT OFFLINE\n\n"
            "O servidor da Resenha Máxima está offline no momento."
        ),
    },
    {
        "categoria": "Moderação",
        "titulo": "Solicitação de Ban / Hackban",
        "destino": "Canal de aprovação",
        "conteudo": (
            "⚠️ Solicitação de Ban / Hackban\n\n"
            "Exibe usuário, ID, solicitante, motivo e status da solicitação "
            "com os controles de aprovação e negação."
        ),
    },
    {
        "categoria": "Administração",
        "titulo": "Canal limpo",
        "destino": "Canal de comandos",
        "conteudo": (
            "🧹 Este canal foi limpo.\n"
            "Essa ação foi feita para evitar acúmulo de mensagens.\n\n"
            "Este aviso desaparece automaticamente após 3 novas mensagens."
        ),
    },
    {
        "categoria": "Bot",
        "titulo": "Funções do Bot",
        "destino": "Canal de funções",
        "conteudo": (
            "🤖 Funções do Bot\n\n"
            "Apresentação oficial com funções atuais, última atualização "
            "e histórico de funções removidas."
        ),
    },
]


def usuarios_vazios():
    return {"versao": 1, "usuarios": {}}


def carregar_usuarios():
    with _users_lock:
        if not USERS_FILE.exists():
            salvar_usuarios_sem_lock(usuarios_vazios())

        try:
            with USERS_FILE.open("r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (OSError, json.JSONDecodeError):
            dados = usuarios_vazios()
            salvar_usuarios_sem_lock(dados)

        if not isinstance(dados, dict):
            dados = usuarios_vazios()

        if not isinstance(dados.get("usuarios"), dict):
            dados["usuarios"] = {}

        return dados


def salvar_usuarios_sem_lock(dados):
    temporario = USERS_FILE.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    temporario.replace(USERS_FILE)


def salvar_usuarios(dados):
    with _users_lock:
        salvar_usuarios_sem_lock(dados)


def normalizar_nome_cargo(nome):
    return (
        str(nome or "")
        .strip()
        .casefold()
        .replace("-", " ")
        .replace("_", " ")
    )


def obter_guild_painel():
    guild = None

    if GUILD_ID:
        try:
            guild = bot.get_guild(int(GUILD_ID))
        except ValueError:
            guild = None

    if guild is None and bot.guilds:
        guild = bot.guilds[0]

    return guild


async def verificar_permissao_discord(discord_id):
    guild = obter_guild_painel()

    if guild is None:
        return {
            "ok": False,
            "nivel": None,
            "nome": None,
            "erro": "O bot do painel ainda não está conectado ao servidor."
        }

    try:
        discord_id = int(discord_id)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "nivel": None,
            "nome": None,
            "erro": "ID do Discord inválido."
        }

    membro = guild.get_member(discord_id)

    if membro is None:
        try:
            membro = await guild.fetch_member(discord_id)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            return {
                "ok": False,
                "nivel": None,
                "nome": None,
                "erro": "Esse usuário não foi encontrado no servidor."
            }

    if membro.id == DONO_ID:
        return {
            "ok": True,
            "nivel": "full",
            "nome": str(membro),
            "erro": None
        }

    ids_cargos = {
        cargo.id
        for cargo in membro.roles
    }

    if CARGO_DESENVOLVIMENTO_ID in ids_cargos:
        return {
            "ok": True,
            "nivel": "full",
            "nome": str(membro),
            "erro": None
        }

    cargo_eventos_encontrado = False

    if CARGO_EVENTOS_ID:
        cargo_eventos_encontrado = (
            CARGO_EVENTOS_ID in ids_cargos
        )
    else:
        for cargo in membro.roles:
            nome = normalizar_nome_cargo(cargo.name)

            if nome in {
                "departamento de eventos",
                "departamento eventos",
                "equipe de eventos",
                "eventos"
            }:
                cargo_eventos_encontrado = True
                break

    if cargo_eventos_encontrado:
        return {
            "ok": True,
            "nivel": "eventos",
            "nome": str(membro),
            "erro": None
        }

    if CARGO_BANIMENTOS_ID in ids_cargos:
        return {
            "ok": True,
            "nivel": "banimentos",
            "nome": str(membro),
            "erro": None
        }

    if CARGO_ENTRADA_ID in ids_cargos:
        return {
            "ok": True,
            "nivel": "entrada",
            "nome": str(membro),
            "erro": None
        }

    if CARGO_PATENTE_MINECRAFT_ID in ids_cargos:
        return {
            "ok": True,
            "nivel": "minecraft",
            "nome": str(membro),
            "erro": None
        }

    return {
        "ok": False,
        "nivel": None,
        "nome": str(membro),
        "erro": (
            "O usuário não possui nenhum dos cargos autorizados para o painel."
        )
    }


def verificar_permissao_discord_sync(discord_id):
    if not bot.is_ready() or BOT_LOOP is None:
        return {
            "ok": False,
            "nivel": None,
            "nome": None,
            "erro": "O bot do painel ainda não está conectado ao Discord."
        }

    try:
        futuro = asyncio.run_coroutine_threadsafe(
            verificar_permissao_discord(discord_id),
            BOT_LOOP
        )
        return futuro.result(timeout=12)

    except Exception as erro:
        return {
            "ok": False,
            "nivel": None,
            "nome": None,
            "erro": f"Falha ao verificar o Discord: {erro}"
        }


def canal_eventos_id(canais=None):
    if CANAL_EVENTOS_ID:
        return str(CANAL_EVENTOS_ID)

    canais = canais or obter_canais_texto_sync()

    nomes_exatos = {
        "eventos",
        "evento",
        "departamento-de-eventos",
        "departamento de eventos",
    }

    for canal in canais:
        nome = str(canal.get("nome", "")).casefold()

        if nome in nomes_exatos:
            return canal["id"]

    return None


def _somente_digitos(valor):
    texto = str(valor or "").strip()
    return texto if texto.isdigit() else None

def _procurar_ids_vinculo_em_objeto(objeto, roblox_id):
    """Procura vínculo Roblox↔Discord em formatos antigos sem apagar dados."""
    alvo = str(int(roblox_id))

    if isinstance(objeto, dict):
        valor_direto = objeto.get(alvo)
        if isinstance(valor_direto, (str, int)) and _somente_digitos(valor_direto):
            return int(valor_direto)

        if isinstance(valor_direto, dict):
            for chave in (
                "discord_id", "discordId", "discord_user_id",
                "id_discord", "usuario_discord_id", "user_id"
            ):
                encontrado = _somente_digitos(valor_direto.get(chave))
                if encontrado:
                    return int(encontrado)

        roblox_keys = (
            "roblox_id", "robloxId", "roblox_user_id",
            "robloxUserId", "id_roblox", "usuario_roblox_id"
        )
        discord_keys = (
            "discord_id", "discordId", "discord_user_id",
            "discordUserId", "id_discord", "usuario_discord_id"
        )

        roblox_encontrado = None
        for chave in roblox_keys:
            valor = _somente_digitos(objeto.get(chave))
            if valor:
                roblox_encontrado = valor
                break

        if roblox_encontrado == alvo:
            for chave in discord_keys:
                valor = _somente_digitos(objeto.get(chave))
                if valor:
                    return int(valor)

        for valor in objeto.values():
            encontrado = _procurar_ids_vinculo_em_objeto(valor, roblox_id)
            if encontrado:
                return encontrado

    elif isinstance(objeto, list):
        for item in objeto:
            encontrado = _procurar_ids_vinculo_em_objeto(item, roblox_id)
            if encontrado:
                return encontrado

    return None

def discord_id_por_roblox_id(roblox_id):
    """Mantém compatibilidade com nomes/formatos antigos de arquivo de vínculo."""
    candidatos = [
        ROBLOX_LINKS_FILE,
        DATA_DIR / "roblox_discord_links.json",
        DATA_DIR / "roblox_vinculos.json",
        DATA_DIR / "vinculos_roblox.json",
        DATA_DIR / "links_roblox.json",
        DATA_DIR / "vinculos.json",
        USERS_FILE,
    ]

    vistos = set()
    for caminho in candidatos:
        caminho = Path(caminho)
        chave = str(caminho)
        if chave in vistos:
            continue
        vistos.add(chave)

        if not caminho.exists():
            continue

        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        encontrado = _procurar_ids_vinculo_em_objeto(dados, roblox_id)
        if encontrado:
            return encontrado

    return None

async def _buscar_membro_direto(guild_id, discord_id):
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return None, "guild_unavailable"

    try:
        membro = await guild.fetch_member(int(discord_id))
        return membro, None
    except discord.NotFound:
        return None, "not_in_guild"
    except discord.Forbidden:
        membro = guild.get_member(int(discord_id))
        return (membro, None) if membro is not None else (None, "forbidden")
    except discord.HTTPException:
        membro = guild.get_member(int(discord_id))
        return (membro, None) if membro is not None else (None, "http_error")

def _codigo_cargo_por_prioridade(membro, prioridade):
    if membro is None:
        return None
    ids = {cargo.id for cargo in getattr(membro, "roles", [])}
    for cargo_id, codigo in prioridade:
        if cargo_id in ids:
            return codigo
    return None

async def obter_game_profile_roblox(roblox_id):
    discord_id = discord_id_por_roblox_id(roblox_id)

    if discord_id is None:
        return {
            "ok": True,
            "linked": False,
            "in_main_server": False,
            "in_event_server": False,
            "main_role": None,
            "second_role": None,
            "game_permission": "Dono" if int(roblox_id) == ROBLOX_OWNER_USER_ID else "Não membro",
            "discord_bonus_eligible": False,
            "discord_bonus_chance": 0.40,
        }

    membro_principal, erro_principal = await _buscar_membro_direto(
        MAIN_DISCORD_GUILD_ID, discord_id
    )
    membro_eventos, erro_eventos = await _buscar_membro_direto(
        EVENTOS_GUILD_ID, discord_id
    )

    in_main = membro_principal is not None
    in_event = membro_eventos is not None
    main_role = _codigo_cargo_por_prioridade(membro_principal, MAIN_ROLE_PRIORITY)
    second_role = _codigo_cargo_por_prioridade(membro_eventos, EVENT_ROLE_PRIORITY)

    if int(roblox_id) == ROBLOX_OWNER_USER_ID:
        permissao = "Dono"
    elif not in_main:
        permissao = "Não membro"
    elif main_role == "ADM_DC":
        permissao = "Admin"
    elif second_role is not None:
        permissao = "Eventos"
    else:
        permissao = "Membro"

    resposta = {
        "ok": True,
        "linked": True,
        "discord_id": str(discord_id),
        "in_main_server": in_main,
        "in_event_server": in_event,
        "main_role": main_role,
        "second_role": second_role,
        "game_permission": permissao,
        "discord_bonus_eligible": True,
        "discord_bonus_chance": 0.40,
    }

    erros = []
    if erro_principal not in (None, "not_in_guild"):
        erros.append(f"principal:{erro_principal}")
    if erro_eventos not in (None, "not_in_guild"):
        erros.append(f"eventos:{erro_eventos}")
    if erros:
        resposta["warning"] = ",".join(erros)

    return resposta

def obter_game_profile_roblox_sync(roblox_id):
    if not TOKEN or not bot.is_ready() or BOT_LOOP is None:
        return {
            "ok": False,
            "linked": bool(discord_id_por_roblox_id(roblox_id)),
            "error": "discord_bot_not_ready",
        }

    try:
        futuro = asyncio.run_coroutine_threadsafe(
            obter_game_profile_roblox(roblox_id), BOT_LOOP
        )
        return futuro.result(timeout=12)
    except Exception as erro:
        return {
            "ok": False,
            "linked": bool(discord_id_por_roblox_id(roblox_id)),
            "error": f"{type(erro).__name__}: {erro}",
        }

@app.get("/api/roblox/game-profile/<int:roblox_id>")
def api_roblox_game_profile(roblox_id):
    return obter_game_profile_roblox_sync(roblox_id)


def nivel_sessao():
    return session.get("nivel")


def acesso_total():
    return nivel_sessao() == "full"


def canal_permitido_para_sessao(canal_id, canais=None):
    if acesso_total():
        return True

    if nivel_sessao() != "eventos":
        return False

    eventos_id = canal_eventos_id(canais)

    return (
        eventos_id is not None
        and str(canal_id) == str(eventos_id)
    )


def filtrar_canais_por_permissao(canais):
    if acesso_total():
        return canais

    if nivel_sessao() == "eventos":
        eventos_id = canal_eventos_id(canais)

        if eventos_id is None:
            return []

        return [
            canal
            for canal in canais
            if str(canal["id"]) == str(eventos_id)
        ]

    return []


def login_obrigatorio(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logado"):
            return redirect(url_for("login"))

        return func(*args, **kwargs)

    return wrapper


def somente_full(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logado"):
            return redirect(url_for("login"))

        if not acesso_total():
            flash("❌ Você não possui acesso a esta área.")
            return redirect(url_for("painel"))

        return func(*args, **kwargs)

    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")

        # Login mestre antigo continua funcionando.
        if secrets.compare_digest(senha, PANEL_PASSWORD):
            session.clear()
            session["logado"] = True
            session["usuario"] = "Administrador principal"
            session["nivel"] = "full"
            session["discord_id"] = str(DONO_ID)
            session["login_mestre"] = True

            return redirect(url_for("painel"))

        dados = carregar_usuarios()
        registro = dados["usuarios"].get(
            usuario.casefold()
        )

        if (
            registro
            and check_password_hash(
                registro.get("senha_hash", ""),
                senha
            )
        ):
            permissao = verificar_permissao_discord_sync(
                registro.get("discord_id")
            )

            if not permissao["ok"]:
                flash(
                    "❌ Sua conta existe, mas o acesso do Discord "
                    f"não pôde ser validado: {permissao['erro']}"
                )
                return render_template("login.html")

            session.clear()
            session["logado"] = True
            session["usuario"] = usuario
            session["discord_id"] = str(
                registro.get("discord_id")
            )
            session["discord_nome"] = permissao.get("nome")
            session["nivel"] = permissao["nivel"]
            session["login_mestre"] = False

            return redirect(url_for("painel"))

        flash("❌ Usuário ou senha incorretos.")

    return render_template("login.html")


@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))


def carregar_logs_administrativos():
    """Carrega a central própria do painel sem consultar a DM."""
    if not ADMIN_LOG_FILE.exists():
        return []

    try:
        with ADMIN_LOG_FILE.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(dados, list):
        return []

    registros = []
    for item in dados[-100:]:
        if not isinstance(item, dict):
            continue
        registros.append({
            "data": str(item.get("data", "")),
            "tipo": str(item.get("tipo", "Sistema")),
            "texto": str(item.get("texto", "")),
        })

    return list(reversed(registros))



def resumo_entradas():
    registros = list(reversed(carregar_entradas()[-500:]))
    ranking = {}

    for item in registros:
        if item.get("origem") != "convite":
            continue
        convidador_id = str(item.get("convidador_id") or "")
        if not convidador_id:
            continue
        atual = ranking.setdefault(convidador_id, {
            "id": convidador_id,
            "nome": item.get("convidador_nome") or "Desconhecido",
            "total": 0,
        })
        atual["total"] += 1

    top = sorted(
        ranking.values(),
        key=lambda item: (-item["total"], item["nome"].casefold())
    )[:10]

    return registros, top


def contexto_painel(
    canal_id=None,
    config_temporaria=None,
    aba="menus"
):
    dados = carregar_menus()
    canais_todos = obter_canais_texto_sync()
    canais = filtrar_canais_por_permissao(
        canais_todos
    )

    if nivel_sessao() == "eventos":
        aba = "menus"

    abas_validas = {
        "menus",
        "modelos",
        "central",
        "entradas",
        "atualizacoes",
        "ia",
    }

    if aba not in abas_validas:
        aba = "menus"

    if aba == "entradas" and nivel_sessao() not in {"full", "entrada"}:
        aba = "menus"

    if aba == "central" and nivel_sessao() not in {"full", "banimentos", "entrada", "minecraft"}:
        aba = "menus"

    if aba == "modelos" and nivel_sessao() not in {"full", "banimentos", "entrada", "minecraft"}:
        aba = "menus"

    if aba == "atualizacoes" and not acesso_total():
        aba = "menus"

    if aba == "ia" and not acesso_total():
        aba = "menus"

    ids_validos = {
        canal["id"]
        for canal in canais
    }

    if canal_id is not None:
        canal_id = str(canal_id)

    if (
        canal_id
        and canal_id not in ids_validos
    ):
        canal_id = None

    if not canal_id:
        configurados = list(
            dados["menus"].keys()
        )

        for configurado in configurados:
            if configurado in ids_validos:
                canal_id = configurado
                break

    if not canal_id and canais:
        canal_id = canais[0]["id"]

    canal_atual = next(
        (
            canal
            for canal in canais
            if canal["id"] == canal_id
        ),
        None
    )

    if config_temporaria is not None:
        config = normalizar_menu(
            config_temporaria
        )

    elif (
        canal_id
        and canal_id in dados["menus"]
    ):
        config = normalizar_menu(
            dados["menus"][canal_id]
        )

    else:
        config = menu_padrao()

    menus_configurados = []

    for id_menu, menu in dados["menus"].items():
        if id_menu not in ids_validos:
            continue

        canal = next(
            (
                item
                for item in canais
                if item["id"] == id_menu
            ),
            None
        )

        menus_configurados.append({
            "canal_id": id_menu,
            "canal_nome": (
                canal["nome"]
                if canal
                else menu.get(
                    "canal_nome",
                    f"Canal {id_menu}"
                )
            ),
            "titulo": (
                menu.get("titulo")
                or "Menu sem título"
            ),
            "ativo": (
                id_menu == canal_id
            )
        })

    menus_configurados.sort(
        key=lambda item: item[
            "canal_nome"
        ].lower()
    )

    usuarios = []

    if acesso_total():
        dados_usuarios = carregar_usuarios()

        for chave, registro in dados_usuarios[
            "usuarios"
        ].items():
            usuarios.append({
                "usuario": registro.get(
                    "usuario",
                    chave
                ),
                "discord_id": str(
                    registro.get("discord_id", "")
                ),
                "nivel_ultimo_login": registro.get(
                    "nivel_ultimo_login",
                    "revalidado no login"
                ),
            })

        usuarios.sort(
            key=lambda item: item[
                "usuario"
            ].casefold()
        )

    logs_admin = (
        carregar_logs_administrativos()
        if aba == "central"
        and nivel_sessao() in {"full", "banimentos", "entrada", "minecraft"}
        else []
    )

    entradas, ranking_convites = resumo_entradas() if aba == "entradas" else ([], [])

    atualizacoes = (
        resumo_atualizacoes_painel()
        if aba == "atualizacoes"
        else {
            "canal_id": "",
            "futuras_texto": "",
            "futuras_publicadas": False,
            "futuras_publicado_em": "",
            "historico": [],
        }
    )

    ia_config = (
        carregar_config_ia()
        if aba == "ia"
        else dict(IA_CONFIG_PADRAO)
    )

    return {
        "aba": aba,
        "config": config,
        "canais": canais,
        "canal_id": canal_id,
        "canal_atual": canal_atual,
        "menus_configurados": menus_configurados,
        "canal_teste_id": CANAL_TESTE_ID,
        "bot_conectado": (
            bot.is_ready()
            if TOKEN
            else False
        ),
        "max_botoes": MAX_BOTOES,
        "nivel": nivel_sessao(),
        "acesso_total": acesso_total(),
        "pode_ver_modelos": nivel_sessao() in {"full", "banimentos", "entrada", "minecraft"},
        "pode_ver_central": nivel_sessao() in {"full", "banimentos", "entrada", "minecraft"},
        "pode_ver_entradas": nivel_sessao() in {"full", "entrada"},
        "pode_ver_atualizacoes": acesso_total(),
        "pode_ver_ia": acesso_total(),
        "usuario_logado": session.get(
            "usuario",
            "Usuário"
        ),
        "discord_nome": session.get(
            "discord_nome"
        ),
        "modelos_mensagens": MODELOS_MENSAGENS,
        "logs_admin": logs_admin,
        "entradas": entradas,
        "ranking_convites": ranking_convites,
        "atualizacoes": atualizacoes,
        "ia_config": ia_config,
        "usuarios_painel": usuarios,
        "cargo_eventos_configurado": bool(
            CARGO_EVENTOS_ID
        ),
        "canal_eventos_configurado": bool(
            CANAL_EVENTOS_ID
        ),
    }


@app.after_request
def _injetar_link_estruturas_no_painel(response):
    """Adiciona o Construtor ao menu sem exigir troca do index.html atual."""
    try:
        if (
            request.endpoint == "painel"
            and acesso_total()
            and response.content_type
            and "text/html" in response.content_type
        ):
            html = response.get_data(as_text=True)
            if (
                "rm-link-estruturas" not in html
                and "</nav>" in html
            ):
                link = (
                    '<a id="rm-link-estruturas" '
                    'class="panel-tab" href="/estruturas">'
                    '🏗️ Estruturas</a>'
                )
                html = html.replace(
                    "</nav>",
                    link + "</nav>",
                    1,
                )
                response.set_data(html)
    except Exception as erro:
        print(
            "Não foi possível injetar link de Estruturas: "
            f"{erro!r}"
        )
    return response


@app.route("/estruturas", methods=["GET"])
@login_obrigatorio
@somente_full
def estruturas_discord():
    dados = carregar_estruturas_discord()
    estruturas = list(
        dados.get("estruturas", {}).values()
    )
    estruturas.sort(
        key=lambda item: item.get("criado_em", ""),
        reverse=True,
    )
    return render_template_string(
        ESTRUTURAS_HTML,
        estruturas=estruturas,
        estilos=ESTILOS_ESTRUTURA,
    )


@app.route(
    "/estruturas/sugerir-nomes",
    methods=["POST"],
)
@login_obrigatorio
@somente_full
def sugerir_nomes_estrutura():
    payload = request.get_json(silent=True) or {}
    nomes, fonte = sugerir_nomes_estrutura_ia(
        payload.get("tema")
        or "Departamento de Eventos"
    )
    return jsonify({
        "ok": True,
        "nomes": nomes,
        "fonte": fonte,
    })


@app.route(
    "/estruturas/eventos/criar",
    methods=["POST"],
)
@login_obrigatorio
@somente_full
def criar_estrutura_eventos():
    if (
        not TOKEN
        or not bot.is_ready()
        or BOT_LOOP is None
    ):
        flash(
            "❌ O bot do SITE ainda não está "
            "conectado ao Discord."
        )
        return redirect(
            url_for("estruturas_discord")
        )

    departamento_ids = _ids_cargos(
        request.form.get("departamento_role_id")
    )
    if len(departamento_ids) != 1:
        flash(
            "❌ Informe exatamente um ID para o cargo "
            "Departamento de Eventos."
        )
        return redirect(
            url_for("estruturas_discord")
        )

    estilo = request.form.get(
        "estilo",
        "rm",
    )
    if estilo not in ESTILOS_ESTRUTURA:
        estilo = "rm"

    config = {
        "categoria_nome": request.form.get(
            "categoria_nome",
            "central de eventos",
        ).strip(),
        "chat_nome": request.form.get(
            "chat_nome",
            "chat da equipe",
        ).strip(),
        "anuncios_nome": request.form.get(
            "anuncios_nome",
            "comunicados",
        ).strip(),
        "sugestoes_nome": request.form.get(
            "sugestoes_nome",
            "sugestoes",
        ).strip(),
        "hierarquia_nome": request.form.get(
            "hierarquia_nome",
            "hierarquia",
        ).strip(),
        "estilo": estilo,
        "departamento_role_id": departamento_ids[0],
        "supervisor_role_ids": _ids_cargos(
            request.form.get("supervisor_role_ids")
        ),
        "admin_role_ids": _ids_cargos(
            request.form.get("admin_role_ids")
        ),
    }

    if not config["admin_role_ids"]:
        flash(
            "❌ Informe pelo menos um cargo ADM "
            "para o canal de hierarquia."
        )
        return redirect(
            url_for("estruturas_discord")
        )

    try:
        futuro = asyncio.run_coroutine_threadsafe(
            criar_estrutura_eventos_discord(
                config
            ),
            BOT_LOOP,
        )
        registro = futuro.result(timeout=35)
        flash(
            "✅ Estrutura criada no Discord. Categoria: "
            f"{registro['categoria_nome']} "
            f"({registro['categoria_id']})."
        )
    except Exception as erro:
        print(
            "Erro ao criar estrutura pelo painel: "
            f"{erro!r}"
        )
        flash(
            "❌ Não foi possível criar a estrutura: "
            f"{erro}"
        )

    return redirect(
        url_for("estruturas_discord")
    )


@app.route("/", methods=["GET", "POST"])
@login_obrigatorio
def painel():
    aba = request.args.get(
        "aba",
        "menus"
    )

    if request.method == "GET":
        canal_id = request.args.get(
            "canal"
        )

        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                aba=aba
            )
        )

    # Somente a aba de menus usa o POST principal.
    canal_id = request.form.get(
        "canal_id",
        ""
    ).strip()

    if not canal_id:
        flash(
            "❌ Selecione um canal antes de editar o menu."
        )
        return redirect(
            url_for(
                "painel",
                aba="menus"
            )
        )

    canais = filtrar_canais_por_permissao(
        obter_canais_texto_sync()
    )

    if not canal_permitido_para_sessao(
        canal_id,
        canais
    ):
        flash(
            "❌ Você não possui permissão "
            "para editar esse canal."
        )
        return redirect(
            url_for(
                "painel",
                aba="menus"
            )
        )

    acao = request.form.get(
        "acao",
        "salvar"
    )

    try:
        novo_config = config_do_formulario(
            request.form
        )

    except ValueError as erro:
        flash(f"❌ {erro}")

        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                config_temporaria=request.form,
                aba="menus"
            )
        )

    canal_atual = next(
        (
            canal
            for canal in canais
            if canal["id"] == canal_id
        ),
        None
    )

    if canais and canal_atual is None:
        flash(
            "❌ O canal selecionado não foi "
            "encontrado no servidor."
        )
        return redirect(
            url_for(
                "painel",
                aba="menus"
            )
        )

    if acao == "testar":
        if not TOKEN:
            flash(
                "❌ TOKEN não configurado. "
                "Não foi possível enviar a prévia."
            )
            return render_template(
                "index.html",
                **contexto_painel(
                    canal_id=canal_id,
                    config_temporaria=novo_config,
                    aba="menus"
                )
            )

        if (
            not bot.is_ready()
            or BOT_LOOP is None
        ):
            flash(
                "❌ O bot ainda não está "
                "conectado ao Discord."
            )
            return render_template(
                "index.html",
                **contexto_painel(
                    canal_id=canal_id,
                    config_temporaria=novo_config,
                    aba="menus"
                )
            )

        try:
            futuro = asyncio.run_coroutine_threadsafe(
                enviar_teste_discord(
                    novo_config
                ),
                BOT_LOOP
            )
            futuro.result(timeout=15)

            flash(
                "🧪 Prévia enviada ao canal de testes. "
                "Nenhum menu oficial foi alterado."
            )

        except Exception as erro:
            print(
                "Erro ao enviar prévia pelo painel: "
                f"{repr(erro)}"
            )

            flash(
                "❌ Não foi possível enviar a prévia: "
                f"{erro}"
            )

        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                config_temporaria=novo_config,
                aba="menus"
            )
        )

    if acao == "excluir":
        dados = carregar_menus()

        if canal_id in dados["menus"]:
            del dados["menus"][canal_id]
            salvar_menus(dados)
            flash(
                "🗑️ Menu removido deste canal."
            )

        else:
            flash(
                "ℹ️ Este canal ainda não "
                "possuía um menu salvo."
            )

        return redirect(
            url_for(
                "painel",
                aba="menus",
                canal=canal_id
            )
        )

    canal_destino_id = request.form.get(
        "salvar_destino_id",
        canal_id
    ).strip()

    if not canal_permitido_para_sessao(
        canal_destino_id,
        canais
    ):
        flash(
            "❌ Você não possui permissão "
            "para salvar nesse canal."
        )

        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                config_temporaria=novo_config,
                aba="menus"
            )
        )

    canal_destino = next(
        (
            canal
            for canal in canais
            if canal["id"] == canal_destino_id
        ),
        None
    )

    if canais and canal_destino is None:
        flash(
            "❌ O canal escolhido para salvar "
            "não foi encontrado no servidor."
        )
        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                config_temporaria=novo_config,
                aba="menus"
            )
        )

    dados = carregar_menus()

    menu_salvo = deepcopy(
        novo_config
    )

    menu_salvo["canal_id"] = (
        canal_destino_id
    )

    menu_salvo["canal_nome"] = (
        canal_destino["nome"]
        if canal_destino
        else f"Canal {canal_destino_id}"
    )

    dados["menus"][
        canal_destino_id
    ] = menu_salvo

    salvar_menus(dados)

    flash(
        "✅ Menu salvo em "
        f"#{menu_salvo['canal_nome']}. "
        "Use /menu nesse canal para publicá-lo."
    )

    return redirect(
        url_for(
            "painel",
            aba="menus",
            canal=canal_destino_id
        )
    )



@app.route(
    "/atualizacoes/canal",
    methods=["POST"]
)
@somente_full
def definir_canal_atualizacoes():
    canal_id = request.form.get(
        "canal_atualizacoes_id",
        ""
    ).strip()

    canais = obter_canais_texto_sync()

    if not any(
        str(canal["id"]) == canal_id
        for canal in canais
    ):
        flash(
            "❌ O canal escolhido não foi encontrado no servidor."
        )
        return redirect(
            url_for(
                "painel",
                aba="atualizacoes"
            )
        )

    dados = carregar_atualizacoes()
    dados["canal_id"] = canal_id
    salvar_atualizacoes(dados)

    flash(
        "✅ Canal de atualizações salvo."
    )

    return redirect(
        url_for(
            "painel",
            aba="atualizacoes"
        )
    )


@app.route(
    "/atualizacoes/futuras/publicar",
    methods=["POST"]
)
@somente_full
def publicar_atualizacoes_futuras():
    texto = request.form.get(
        "texto_futuras",
        ""
    ).strip()

    if not texto:
        flash(
            "❌ Escreva as futuras atualizações antes de publicar."
        )
        return redirect(
            url_for(
                "painel",
                aba="atualizacoes"
            )
        )

    dados = carregar_atualizacoes()

    canal_id = request.form.get(
        "canal_atualizacoes_id",
        ""
    ).strip() or str(
        dados.get("canal_id")
        or ""
    )

    try:
        canal = executar_no_bot(
            localizar_canal_atualizacoes(
                canal_id
            )
        )

        antigas = (
            dados.get("futuras")
            or {}
        )

        antigo_canal_id = str(
            antigas.get("canal_id")
            or canal.id
        )

        # Se já existia uma mensagem de "futuras", ela pode ser substituída.
        # O histórico de NOTAS LANÇADAS nunca é apagado por essa rotina.
        if antigas.get("mensagens_ids"):
            try:
                canal_antigo = executar_no_bot(
                    localizar_canal(
                        antigo_canal_id
                    )
                )
                executar_no_bot(
                    apagar_mensagens_por_ids(
                        canal_antigo,
                        antigas.get(
                            "mensagens_ids",
                            []
                        )
                    )
                )
            except Exception as erro:
                print(
                    "Aviso ao remover futuras antigas: "
                    f"{repr(erro)}"
                )

        # Fallback importante: se o volume/ID antigo se perdeu, limpa pelo próprio conteúdo do Discord.
        executar_no_bot(
            apagar_futuras_por_varredura(canal)
        )

        frase_final, sticker_id = escolher_rodape_futuras(
            antigas
        )
        texto_publicado = montar_texto_futuras(
            texto,
            frase_final
        )

        mensagens_ids = executar_no_bot(
            enviar_texto_normal_discord(
                canal,
                texto_publicado
            )
        )

        sticker_mensagem_id = executar_no_bot(
            enviar_sticker_futuras(
                canal,
                sticker_id
            )
        )
        if sticker_mensagem_id:
            mensagens_ids.append(
                sticker_mensagem_id
            )

    except Exception as erro:
        print(
            "Erro ao publicar futuras atualizações: "
            f"{repr(erro)}"
        )
        flash(
            "❌ Não foi possível publicar no Discord: "
            f"{erro}"
        )
        return redirect(
            url_for(
                "painel",
                aba="atualizacoes"
            )
        )

    dados["canal_id"] = str(
        canal.id
    )
    dados["futuras"] = {
        "texto": texto,
        "mensagens_ids": mensagens_ids,
        "canal_id": str(canal.id),
        "publicado_em": agora_iso(),
        "frase_final": frase_final,
        "sticker_id": sticker_id,
    }
    salvar_atualizacoes(dados)

    flash(
        "🔮 Futuras atualizações publicadas. "
        "Se você publicar outra prévia, esta será substituída."
    )

    return redirect(
        url_for(
            "painel",
            aba="atualizacoes"
        )
    )


@app.route(
    "/atualizacoes/futuras/remover",
    methods=["POST"]
)
@somente_full
def remover_atualizacoes_futuras():
    dados = carregar_atualizacoes()
    futuras = dados.get("futuras") or {}

    if not futuras.get("mensagens_ids"):
        flash(
            "ℹ️ Não existe mensagem de futuras atualizações ativa."
        )
        return redirect(
            url_for(
                "painel",
                aba="atualizacoes"
            )
        )

    try:
        canal = executar_no_bot(
            localizar_canal(
                futuras.get("canal_id")
                or dados.get("canal_id")
            )
        )
        executar_no_bot(
            apagar_mensagens_por_ids(
                canal,
                futuras.get(
                    "mensagens_ids",
                    []
                )
            )
        )
    except Exception as erro:
        print(
            "Erro ao remover futuras atualizações: "
            f"{repr(erro)}"
        )
        flash(
            "❌ Não foi possível remover a mensagem do Discord: "
            f"{erro}"
        )
        return redirect(
            url_for(
                "painel",
                aba="atualizacoes"
            )
        )

    dados["futuras"] = atualizacoes_vazias()["futuras"]
    salvar_atualizacoes(dados)

    flash(
        "🗑️ Futuras atualizações removidas do Discord."
    )

    return redirect(
        url_for(
            "painel",
            aba="atualizacoes"
        )
    )


@app.route(
    "/atualizacoes/lancar",
    methods=["POST"]
)
@somente_full
def lancar_atualizacao():
    notas = request.form.get(
        "texto_notas",
        ""
    ).strip()

    if not notas:
        flash(
            "❌ Cole as notas da atualização antes de lançar."
        )
        return redirect(
            url_for(
                "painel",
                aba="atualizacoes"
            )
        )

    notas = montar_texto_notas(notas)

    dados = carregar_atualizacoes()

    canal_id = request.form.get(
        "canal_atualizacoes_id",
        ""
    ).strip() or str(
        dados.get("canal_id")
        or ""
    )

    futuras = dados.get("futuras") or {}

    try:
        canal = executar_no_bot(
            localizar_canal_atualizacoes(
                canal_id
            )
        )

        # Primeiro publica as notas. Só depois remove a prévia futura.
        # Assim uma falha no envio não faz a prévia desaparecer antes da hora.
        notas_ids = executar_no_bot(
            enviar_texto_normal_discord(
                canal,
                notas
            )
        )

        # Remove qualquer prévia antiga pelo conteúdo, mesmo se o ID salvo tiver sido perdido.
        executar_no_bot(
            apagar_futuras_por_varredura(canal)
        )

        if futuras.get("mensagens_ids"):
            try:
                canal_futuras = executar_no_bot(
                    localizar_canal(
                        futuras.get("canal_id")
                        or canal.id
                    )
                )
                executar_no_bot(
                    apagar_mensagens_por_ids(
                        canal_futuras,
                        futuras.get(
                            "mensagens_ids",
                            []
                        )
                    )
                )
            except Exception as erro:
                print(
                    "Notas publicadas, mas não foi possível "
                    "remover a prévia futura: "
                    f"{repr(erro)}"
                )
                flash(
                    "⚠️ Notas publicadas, mas a mensagem de futuras "
                    "atualizações não pôde ser removida automaticamente."
                )

    except Exception as erro:
        print(
            "Erro ao lançar atualização: "
            f"{repr(erro)}"
        )
        flash(
            "❌ Não foi possível publicar as notas: "
            f"{erro}"
        )
        return redirect(
            url_for(
                "painel",
                aba="atualizacoes"
            )
        )

    historico = dados.setdefault(
        "historico",
        []
    )

    historico.append({
        "data": agora_iso(),
        "texto": notas,
        "mensagens_ids": notas_ids,
        "canal_id": str(canal.id),
        "futuras_anteriores": str(
            futuras.get("texto")
            or ""
        ),
    })

    dados["historico"] = historico[-100:]
    dados["canal_id"] = str(canal.id)
    dados["futuras"] = atualizacoes_vazias()["futuras"]
    salvar_atualizacoes(dados)

    flash(
        "🚀 Atualização lançada. "
        "As notas ficaram no histórico e a mensagem de futuras atualizações saiu do canal."
    )

    return redirect(
        url_for(
            "painel",
            aba="atualizacoes"
        )
    )


@app.route(
    "/usuarios/criar",
    methods=["POST"]
)
@somente_full
def criar_usuario_painel():
    usuario = request.form.get(
        "novo_usuario",
        ""
    ).strip()

    senha = request.form.get(
        "nova_senha",
        ""
    )

    discord_id = request.form.get(
        "discord_id",
        ""
    ).strip()

    if len(usuario) < 3:
        flash(
            "❌ O usuário precisa ter pelo menos 3 caracteres."
        )
        return redirect(
            url_for(
                "painel",
                aba="central"
            )
        )

    if len(senha) < 6:
        flash(
            "❌ A senha precisa ter pelo menos 6 caracteres."
        )
        return redirect(
            url_for(
                "painel",
                aba="central"
            )
        )

    permissao = verificar_permissao_discord_sync(
        discord_id
    )

    if not permissao["ok"]:
        flash(
            "❌ Conta não criada: "
            f"{permissao['erro']}"
        )
        return redirect(
            url_for(
                "painel",
                aba="central"
            )
        )

    dados = carregar_usuarios()
    chave = usuario.casefold()

    if chave in dados["usuarios"]:
        flash(
            "❌ Já existe um usuário com esse nome."
        )
        return redirect(
            url_for(
                "painel",
                aba="central"
            )
        )

    dados["usuarios"][chave] = {
        "usuario": usuario,
        "discord_id": str(discord_id),
        "senha_hash": generate_password_hash(
            senha
        ),
        "nivel_ultimo_login": permissao[
            "nivel"
        ],
    }

    salvar_usuarios(dados)

    flash(
        "✅ Usuário criado: "
        f"{usuario} • Discord: {permissao['nome']} • "
        f"Nível: {permissao['nivel']}."
    )

    return redirect(
        url_for(
            "painel",
            aba="central"
        )
    )


@app.route(
    "/usuarios/excluir/<usuario>",
    methods=["POST"]
)
@somente_full
def excluir_usuario_painel(usuario):
    dados = carregar_usuarios()
    chave = usuario.casefold()

    if chave in dados["usuarios"]:
        del dados["usuarios"][chave]
        salvar_usuarios(dados)

        flash(
            f"🗑️ Usuário {usuario} removido do painel."
        )

    return redirect(
        url_for(
            "painel",
            aba="central"
        )
    )


@app.route(
    "/ia/config",
    methods=["POST"]
)
@somente_full
def salvar_configuracao_ia():
    canal_id = request.form.get(
        "ia_canal_id",
        ""
    ).strip()

    if canal_id:
        canais = obter_canais_texto_sync()
        if not any(
            str(canal["id"]) == canal_id
            for canal in canais
        ):
            flash("❌ O canal escolhido para a IA não foi encontrado.")
            return redirect(url_for("painel", aba="ia"))

    dados = {
        "ativa": _bool_form("ia_ativa"),
        "canal_id": canal_id,
        "caos_ativo": _bool_form("ia_caos_ativo"),
        "caos_hora_inicio": _int_form(
            "ia_caos_hora_inicio", 6, 0, 23
        ),
        "caos_hora_fim": _int_form(
            "ia_caos_hora_fim", 23, 1, 24
        ),
        "caos_intervalo_minutos": _int_form(
            "ia_caos_intervalo_minutos", 120, 15, 1440
        ),
        "caos_chance": _float_form(
            "ia_caos_chance", 0.12, 0.0, 1.0
        ),
        "call_cooldown_minutos": _int_form(
            "ia_call_cooldown_minutos", 10, 5, 1440
        ),
    }

    salvar_config_ia(dados)
    flash("🤖 Configuração da IA salva. O bot consulta o painel automaticamente.")
    return redirect(url_for("painel", aba="ia"))


@app.route("/api/ia-config")
def api_ia_config():
    resposta = carregar_config_ia()
    resposta["ok"] = True
    return jsonify(resposta)



# =========================================================
# ROBLOX — API INTERNA + OAUTH
# =========================================================

@app.route(
    "/api/roblox/criar-vinculo",
    methods=["POST"],
)
def api_roblox_criar_vinculo():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_roblox(payload):
        return jsonify({
            "ok": False,
            "erro": "Não autorizado.",
        }), 401

    if not _roblox_configurado():
        return jsonify({
            "ok": False,
            "erro": (
                "OAuth Roblox ainda não configurado no SITE. "
                "Defina ROBLOX_CLIENT_ID, ROBLOX_CLIENT_SECRET "
                "e ROBLOX_REDIRECT_URI."
            ),
        }), 503

    discord_id = str(
        payload.get("discord_id")
        or ""
    ).strip()
    guild_id = str(
        payload.get("guild_id")
        or ""
    ).strip()
    discord_nome = str(
        payload.get("discord_nome")
        or ""
    ).strip()[:150]

    if not discord_id.isdigit():
        return jsonify({
            "ok": False,
            "erro": "Discord ID inválido.",
        }), 400

    dados = carregar_roblox_vinculos()

    # Mantém somente a solicitação mais recente do usuário.
    for token_antigo, pendente in list(
        dados["pendentes"].items()
    ):
        if str(
            pendente.get("discord_id")
            or ""
        ) == discord_id:
            dados["pendentes"].pop(
                token_antigo,
                None,
            )

    token = secrets.token_urlsafe(24)
    oauth_state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    agora = datetime.now(timezone.utc)
    expira = agora + timedelta(
        minutes=ROBLOX_PENDENCIA_MINUTOS
    )

    dados["pendentes"][token] = {
        "discord_id": discord_id,
        "discord_nome": discord_nome,
        "guild_id": guild_id,
        "oauth_state": oauth_state,
        "nonce": nonce,
        "criado_em": agora.isoformat(),
        "expira_em": expira.isoformat(),
    }
    salvar_roblox_vinculos(dados)

    url = (
        request.url_root.rstrip("/")
        + url_for(
            "roblox_iniciar",
            token=token,
        )
    )
    return jsonify({
        "ok": True,
        "url": url,
        "expira_em": expira.isoformat(),
    })


@app.route("/roblox/iniciar/<token>")
def roblox_iniciar(token):
    dados = carregar_roblox_vinculos()
    pendente = dados["pendentes"].get(
        str(token)
    )
    if not pendente:
        return (
            _pagina_roblox(
                "Link expirado",
                (
                    "Este link de vinculação não existe mais "
                    "ou passou do prazo de 15 minutos.\n\n"
                    "Use /roblox vincular novamente no Discord."
                ),
            ),
            410,
        )

    if not _roblox_configurado():
        return (
            _pagina_roblox(
                "Roblox ainda não configurado",
                (
                    "O sistema foi instalado, mas as credenciais "
                    "OAuth do Roblox ainda não foram configuradas "
                    "no site."
                ),
            ),
            503,
        )

    parametros = {
        "client_id": ROBLOX_CLIENT_ID,
        "redirect_uri": ROBLOX_REDIRECT_URI,
        "scope": "openid profile",
        "response_type": "code",
        "state": pendente["oauth_state"],
        "nonce": pendente["nonce"],
    }
    destino = (
        ROBLOX_AUTHORIZE_URL
        + "?"
        + urllib.parse.urlencode(parametros)
    )
    return redirect(destino)


@app.route("/roblox/callback")
def roblox_callback():
    erro_oauth = str(
        request.args.get("error")
        or ""
    ).strip()
    if erro_oauth:
        descricao = str(
            request.args.get("error_description")
            or "A autorização foi cancelada ou recusada."
        )
        return (
            _pagina_roblox(
                "Vinculação cancelada",
                descricao,
            ),
            400,
        )

    code = str(
        request.args.get("code")
        or ""
    ).strip()
    state = str(
        request.args.get("state")
        or ""
    ).strip()
    if not code or not state:
        return (
            _pagina_roblox(
                "Resposta inválida",
                "O Roblox não devolveu o código de autorização esperado.",
            ),
            400,
        )

    dados = carregar_roblox_vinculos()
    token_pendente = None
    pendente = None
    for token, item in dados["pendentes"].items():
        if secrets.compare_digest(
            str(item.get("oauth_state") or ""),
            state,
        ):
            token_pendente = token
            pendente = item
            break

    if not pendente:
        return (
            _pagina_roblox(
                "Sessão expirada",
                (
                    "Não encontrei uma solicitação válida para "
                    "esta autorização. Use /roblox vincular novamente."
                ),
            ),
            410,
        )

    try:
        tokens = _post_form_roblox(
            ROBLOX_TOKEN_URL,
            {
                "code": code,
                "grant_type": "authorization_code",
                "client_id": ROBLOX_CLIENT_ID,
                "client_secret": ROBLOX_CLIENT_SECRET,
                "redirect_uri": ROBLOX_REDIRECT_URI,
            },
        )
        access_token = str(
            tokens.get("access_token")
            or ""
        ).strip()
        if not access_token:
            raise RuntimeError(
                "O Roblox não devolveu access_token."
            )

        perfil = _userinfo_roblox(
            access_token
        )
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        RuntimeError,
    ) as erro:
        print(
            "Erro no OAuth Roblox: "
            f"{type(erro).__name__}: {erro}"
        )
        return (
            _pagina_roblox(
                "Falha ao verificar a conta",
                (
                    "Não consegui concluir a verificação com o "
                    "Roblox agora. Tente novamente pelo Discord."
                ),
            ),
            502,
        )

    roblox_id = str(
        perfil.get("sub")
        or ""
    ).strip()
    if not roblox_id.isdigit():
        return (
            _pagina_roblox(
                "Perfil Roblox inválido",
                (
                    "O Roblox autenticou a sessão, mas não enviou "
                    "um User ID válido."
                ),
            ),
            502,
        )

    discord_id = str(
        pendente.get("discord_id")
        or ""
    )

    # Uma conta Roblox não pode representar dois Discords ao mesmo tempo.
    for outro_discord_id, vinculo in dados[
        "vinculos"
    ].items():
        if (
            outro_discord_id != discord_id
            and str(
                vinculo.get("roblox_id")
                or ""
            ) == roblox_id
        ):
            return (
                _pagina_roblox(
                    "Conta já vinculada",
                    (
                        "Essa conta Roblox já está vinculada a "
                        "outro membro do Discord.\n\n"
                        "Se isso estiver incorreto, procure a administração."
                    ),
                ),
                409,
            )

    username = str(
        perfil.get("preferred_username")
        or perfil.get("nickname")
        or perfil.get("name")
        or roblox_id
    )[:80]
    display_name = str(
        perfil.get("name")
        or perfil.get("nickname")
        or username
    )[:80]
    profile_url = str(
        perfil.get("profile")
        or (
            f"https://www.roblox.com/users/"
            f"{roblox_id}/profile"
        )
    )[:500]
    picture = str(
        perfil.get("picture")
        or ""
    )[:1000]

    dados["vinculos"][discord_id] = {
        "discord_id": discord_id,
        "discord_nome": str(
            pendente.get("discord_nome")
            or ""
        )[:150],
        "guild_id": str(
            pendente.get("guild_id")
            or ""
        ),
        "roblox_id": roblox_id,
        "username": username,
        "display_name": display_name,
        "profile": profile_url,
        "picture": picture,
        "verificado_em": _agora_utc_iso(),
    }
    if token_pendente:
        dados["pendentes"].pop(
            token_pendente,
            None,
        )
    salvar_roblox_vinculos(dados)

    return _pagina_roblox(
        "Conta Roblox vinculada!",
        (
            f"Roblox: @{username}\n"
            f"Display: {display_name}\n\n"
            "Volte ao Discord e clique em “Já vinculei”. "
            "Depois disso, a conta aparecerá no /perfil."
        ),
        sucesso=True,
    )


@app.route("/api/roblox/vinculo/<discord_id>")
def api_roblox_vinculo(discord_id):
    if not _autorizado_roblox():
        return jsonify({
            "ok": False,
            "erro": "Não autorizado.",
        }), 401

    discord_id = str(
        discord_id
        or ""
    ).strip()
    if not discord_id.isdigit():
        return jsonify({
            "ok": False,
            "erro": "Discord ID inválido.",
        }), 400

    dados = carregar_roblox_vinculos()
    vinculo = dados["vinculos"].get(
        discord_id
    )
    return jsonify({
        "ok": True,
        "vinculado": bool(vinculo),
        "vinculo": vinculo,
    })


@app.route(
    "/api/roblox/desvincular",
    methods=["POST"],
)
def api_roblox_desvincular():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_roblox(payload):
        return jsonify({
            "ok": False,
            "erro": "Não autorizado.",
        }), 401

    discord_id = str(
        payload.get("discord_id")
        or ""
    ).strip()
    if not discord_id.isdigit():
        return jsonify({
            "ok": False,
            "erro": "Discord ID inválido.",
        }), 400

    dados = carregar_roblox_vinculos()
    removido = dados["vinculos"].pop(
        discord_id,
        None,
    )
    for token, item in list(
        dados["pendentes"].items()
    ):
        if str(
            item.get("discord_id")
            or ""
        ) == discord_id:
            dados["pendentes"].pop(
                token,
                None,
            )

    salvar_roblox_vinculos(dados)

    if removido is None:
        return jsonify({
            "ok": False,
            "erro": "Nenhum vínculo encontrado.",
        }), 404

    return jsonify({
        "ok": True,
        "removido": removido,
    })


# =========================================================
# LINK PÚBLICO — PROVA COM CÓDIGO PRÉ-PREENCHIDO
# =========================================================


@app.route("/recrutamento/eventos/abrir-prova/<codigo>")
def recrutamento_eventos_abrir_prova(codigo):
    codigo = str(codigo or "").strip().upper()
    if not re.fullmatch(r"RM-EVT-[A-F0-9]{6}", codigo):
        return (
            "Código de candidatura inválido.",
            400,
        )

    dados = carregar_recrutamento_eventos()
    candidatura = dados.get("candidaturas", {}).get(codigo)
    if not candidatura:
        return (
            "Esta candidatura não existe ou expirou.",
            404,
        )

    if candidatura.get("status") not in {
        "aguardando_prova",
        "prova_recebida",
    }:
        return (
            "Esta candidatura já avançou para outra etapa.",
            409,
        )

    script_url = str(
        (dados.get("config") or {}).get("prefill_script_url")
        or ""
    ).strip()
    if not script_url:
        # Fallback seguro enquanto o Apps Script ainda não foi registrado.
        # O candidato ainda consegue abrir o Forms e possui o código no Discord.
        return redirect(EVENTOS_FORMS_URL)

    separador = "&" if "?" in script_url else "?"
    destino = f"{script_url}{separador}codigo={codigo}"
    return redirect(destino)


# =========================================================
# API — RECRUTAMENTO DO DEPARTAMENTO DE EVENTOS
# =========================================================


@app.route(
    "/api/recrutamento/eventos/configurar-prefill",
    methods=["POST"],
)
def api_eventos_configurar_prefill():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_recrutamento_eventos(payload):
        return jsonify({"ok": False, "erro": "Não autorizado."}), 401

    script_url = str(payload.get("prefill_script_url") or "").strip()
    if not (
        script_url.startswith("https://script.google.com/macros/s/")
        and "/exec" in script_url
    ):
        return jsonify({
            "ok": False,
            "erro": "URL do Web App do Apps Script inválida.",
        }), 400

    dados = carregar_recrutamento_eventos()
    dados.setdefault("config", {})["prefill_script_url"] = script_url
    salvar_recrutamento_eventos(dados)
    return jsonify({
        "ok": True,
        "prefill_script_url": script_url,
    })


@app.route(
    "/api/recrutamento/eventos/candidatura",
    methods=["POST"],
)
def api_eventos_criar_candidatura():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_recrutamento_eventos(payload):
        return jsonify({"ok": False, "erro": "Não autorizado."}), 401

    discord_id = str(payload.get("discord_id") or "").strip()
    discord_nome = str(payload.get("discord_nome") or "").strip()[:120]
    if not discord_id.isdigit():
        return jsonify({
            "ok": False,
            "erro": "Discord ID inválido.",
        }), 400

    dados = carregar_recrutamento_eventos()
    ignorar_cooldown = (
        discord_id == str(CONTA_TESTE_ID)
        and bool(payload.get("ignorar_cooldown"))
    )
    cooldown = dados.get("cooldowns", {}).get(discord_id, "")
    restantes = _segundos_restantes_cooldown(cooldown)
    if restantes > 0 and not ignorar_cooldown:
        return jsonify({
            "ok": False,
            "erro": "cooldown",
            "segundos_restantes": restantes,
            "cooldown_ate": cooldown,
        }), 429

    for candidatura in dados.get("candidaturas", {}).values():
        if str(candidatura.get("discord_id")) != discord_id:
            continue

        status_atual = str(candidatura.get("status") or "").strip()

        # Só reutiliza o código enquanto a pessoa AINDA não enviou a prova.
        # Depois que a candidatura avançou, devolver o mesmo código criava
        # um botão que inevitavelmente abria uma página 409.
        if status_atual == "aguardando_prova":
            return jsonify({
                "ok": True,
                "codigo": candidatura.get("codigo"),
                "reutilizada": True,
                "status": status_atual,
            })

        if status_atual in {
            "prova_recebida",
            "em_avaliacao",
            "em_call",
        }:
            return jsonify({
                "ok": False,
                "erro": "candidatura_em_andamento",
                "codigo": candidatura.get("codigo"),
                "status": status_atual,
            }), 409

    codigo = _gerar_codigo_candidatura(dados)
    candidatura = {
        "codigo": codigo,
        "discord_id": discord_id,
        "discord_nome": discord_nome,
        "criado_em": _agora_utc_iso(),
        "status": "aguardando_prova",
        "respostas": [],
        "prova_recebida_em": "",
        "discord_channel_id": "",
        "voice_channel_id": "",
        "horario_entrevista": "",
        "horario_informado_em": "",
        "avaliador_id": "",
        "avaliador_nome": "",
        "resultado_em": "",
    }
    dados.setdefault("candidaturas", {})[codigo] = candidatura
    salvar_recrutamento_eventos(dados)
    return jsonify({
        "ok": True,
        "codigo": codigo,
        "reutilizada": False,
    })


def _campo_oculto_relatorio_eventos(pergunta):
    titulo = str(pergunta or "").strip().casefold()
    substituicoes = str.maketrans({
        "á": "a", "à": "a", "ã": "a", "â": "a",
        "é": "e", "ê": "e",
        "í": "i",
        "ó": "o", "ô": "o", "õ": "o",
        "ú": "u", "ç": "c",
    })
    titulo = titulo.translate(substituicoes)
    return titulo in {
        "carimbo de data/hora",
        "timestamp",
        "horario",
        "data e hora",
        "endereco de e-mail",
        "endereco de email",
        "e-mail",
        "email",
        "pontuacao",
        "score",
    }


@app.route(
    "/api/recrutamento/eventos/resetar-teste",
    methods=["POST"],
)
def api_eventos_resetar_teste():
    """Apaga o histórico de recrutamento de uma conta escolhida pelo dono.

    Esta rota existe para testes repetidos do fluxo. Ela é protegida pela
    mesma chave secreta usada pelo bot/site e nunca é exposta no painel
    público do candidato.
    """
    payload = request.get_json(silent=True) or {}
    if not _autorizado_recrutamento_eventos(payload):
        return jsonify({"ok": False, "erro": "Não autorizado."}), 401

    discord_id = str(payload.get("discord_id") or "").strip()
    if not discord_id.isdigit():
        return jsonify({
            "ok": False,
            "erro": "Discord ID inválido.",
        }), 400

    dados = carregar_recrutamento_eventos()
    removidas = []

    for codigo, candidatura in list(
        dados.get("candidaturas", {}).items()
    ):
        if str(candidatura.get("discord_id") or "") != discord_id:
            continue
        removidas.append({
            "codigo": codigo,
            "status": str(candidatura.get("status") or ""),
            "discord_channel_id": str(
                candidatura.get("discord_channel_id") or ""
            ),
            "voice_channel_id": str(
                candidatura.get("voice_channel_id") or ""
            ),
        })
        dados["candidaturas"].pop(codigo, None)

    dados.setdefault("cooldowns", {}).pop(discord_id, None)
    salvar_recrutamento_eventos(dados)

    return jsonify({
        "ok": True,
        "discord_id": discord_id,
        "removidas": removidas,
        "total_removidas": len(removidas),
    })


@app.route(
    "/api/recrutamento/eventos/prova",
    methods=["POST"],
)
def api_eventos_receber_prova():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_recrutamento_eventos(payload):
        return jsonify({"ok": False, "erro": "Não autorizado."}), 401

    codigo = str(payload.get("codigo") or "").strip().upper()
    respostas = payload.get("respostas") or []
    if not codigo:
        return jsonify({
            "ok": False,
            "erro": "Código da candidatura ausente.",
        }), 400
    if not isinstance(respostas, list) or not respostas:
        return jsonify({
            "ok": False,
            "erro": "Nenhuma resposta recebida.",
        }), 400

    dados = carregar_recrutamento_eventos()
    candidatura = dados.get("candidaturas", {}).get(codigo)
    if not candidatura:
        return jsonify({
            "ok": False,
            "erro": "Código de candidatura inválido.",
        }), 404

    if candidatura.get("status") not in {
        "aguardando_prova",
        "prova_recebida",
    }:
        return jsonify({
            "ok": False,
            "erro": "Esta candidatura não aceita uma nova prova.",
        }), 409

    respostas_limpas = []
    for item in respostas[:100]:
        if not isinstance(item, dict):
            continue
        pergunta = str(item.get("pergunta") or "").strip()[:1000]
        resposta = str(item.get("resposta") or "").strip()[:5000]
        if pergunta and not _campo_oculto_relatorio_eventos(pergunta):
            respostas_limpas.append({
                "pergunta": pergunta,
                "resposta": resposta,
            })

    if not respostas_limpas:
        return jsonify({
            "ok": False,
            "erro": "Respostas inválidas.",
        }), 400

    candidatura["respostas"] = respostas_limpas
    candidatura["prova_recebida_em"] = str(
        payload.get("enviado_em") or _agora_utc_iso()
    )
    candidatura["status"] = "prova_recebida"
    candidatura["discord_channel_id"] = ""
    salvar_recrutamento_eventos(dados)
    return jsonify({"ok": True, "codigo": codigo})


@app.route(
    "/api/recrutamento/eventos/horario",
    methods=["POST"],
)
def api_eventos_salvar_horario():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_recrutamento_eventos(payload):
        return jsonify({"ok": False, "erro": "Não autorizado."}), 401

    codigo = str(payload.get("codigo") or "").strip().upper()
    discord_id = str(payload.get("discord_id") or "").strip()
    horario = " ".join(str(payload.get("horario") or "").strip().split())[:180]

    if not codigo or not discord_id.isdigit() or len(horario) < 2:
        return jsonify({
            "ok": False,
            "erro": "Código, usuário ou horário inválido.",
        }), 400

    dados = carregar_recrutamento_eventos()
    candidatura = dados.get("candidaturas", {}).get(codigo)
    if not candidatura:
        return jsonify({
            "ok": False,
            "erro": "Candidatura não encontrada.",
        }), 404

    if str(candidatura.get("discord_id") or "") != discord_id:
        return jsonify({
            "ok": False,
            "erro": "Esta candidatura pertence a outro usuário.",
        }), 403

    if candidatura.get("status") in {"aprovado", "reprovado", "encerrado"}:
        return jsonify({
            "ok": False,
            "erro": "A candidatura já foi finalizada.",
        }), 409

    candidatura["horario_entrevista"] = horario
    candidatura["horario_informado_em"] = _agora_utc_iso()
    salvar_recrutamento_eventos(dados)
    return jsonify({
        "ok": True,
        "codigo": codigo,
        "horario": horario,
    })


@app.route("/api/recrutamento/eventos/pendentes")
def api_eventos_provas_pendentes():
    if not _autorizado_recrutamento_eventos():
        return jsonify({"ok": False, "erro": "Não autorizado."}), 401

    dados = carregar_recrutamento_eventos()
    pendentes = [
        candidatura
        for candidatura in dados.get("candidaturas", {}).values()
        if candidatura.get("status") == "prova_recebida"
        and not candidatura.get("discord_channel_id")
    ][:20]
    return jsonify({
        "ok": True,
        "candidaturas": pendentes,
    })


@app.route(
    "/api/recrutamento/eventos/entregue",
    methods=["POST"],
)
def api_eventos_marcar_entregue():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_recrutamento_eventos(payload):
        return jsonify({"ok": False, "erro": "Não autorizado."}), 401

    codigo = str(payload.get("codigo") or "").strip().upper()
    canal_id = str(
        payload.get("discord_channel_id") or ""
    ).strip()
    dados = carregar_recrutamento_eventos()
    candidatura = dados.get("candidaturas", {}).get(codigo)
    if not candidatura:
        return jsonify({
            "ok": False,
            "erro": "Candidatura não encontrada.",
        }), 404

    candidatura["discord_channel_id"] = canal_id
    candidatura["status"] = "em_avaliacao"
    salvar_recrutamento_eventos(dados)
    return jsonify({"ok": True})


@app.route(
    "/api/recrutamento/eventos/por-canal/<canal_id>"
)
def api_eventos_por_canal(canal_id):
    if not _autorizado_recrutamento_eventos():
        return jsonify({"ok": False, "erro": "Não autorizado."}), 401

    dados = carregar_recrutamento_eventos()
    candidatura = _candidatura_por_canal(dados, canal_id)
    if not candidatura:
        return jsonify({
            "ok": False,
            "erro": "Candidatura não encontrada.",
        }), 404
    return jsonify({
        "ok": True,
        "candidatura": candidatura,
    })


@app.route(
    "/api/recrutamento/eventos/status",
    methods=["POST"],
)
def api_eventos_atualizar_status():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_recrutamento_eventos(payload):
        return jsonify({"ok": False, "erro": "Não autorizado."}), 401

    codigo = str(payload.get("codigo") or "").strip().upper()
    novo_status = str(payload.get("status") or "").strip()
    permitidos = {
        "em_avaliacao",
        "em_call",
        "aprovado",
        "reprovado",
        "encerrado",
    }
    if novo_status not in permitidos:
        return jsonify({
            "ok": False,
            "erro": "Status inválido.",
        }), 400

    dados = carregar_recrutamento_eventos()
    candidatura = dados.get("candidaturas", {}).get(codigo)
    if not candidatura:
        return jsonify({
            "ok": False,
            "erro": "Candidatura não encontrada.",
        }), 404

    candidatura["status"] = novo_status
    candidatura["avaliador_id"] = str(
        payload.get("avaliador_id")
        or candidatura.get("avaliador_id")
        or ""
    )
    candidatura["avaliador_nome"] = str(
        payload.get("avaliador_nome")
        or candidatura.get("avaliador_nome")
        or ""
    )[:120]
    candidatura["resultado_em"] = _agora_utc_iso()

    if payload.get("voice_channel_id") is not None:
        candidatura["voice_channel_id"] = str(
            payload.get("voice_channel_id") or ""
        )

    if novo_status == "reprovado":
        fim = (
            datetime.now(timezone.utc)
            + timedelta(hours=EVENTOS_REFAZER_HORAS)
        )
        dados.setdefault("cooldowns", {})[
            str(candidatura.get("discord_id"))
        ] = fim.isoformat()
    elif novo_status == "aprovado":
        dados.setdefault("cooldowns", {}).pop(
            str(candidatura.get("discord_id")),
            None,
        )

    salvar_recrutamento_eventos(dados)
    return jsonify({
        "ok": True,
        "candidatura": candidatura,
    })
def status():
    dados = carregar_menus()

    return {
        "site": "online",
        "bot_configurado": bool(TOKEN),
        "bot_conectado": (
            bot.is_ready()
            if TOKEN
            else False
        ),
        "canal_teste_id": CANAL_TESTE_ID,
        "menus_configurados": len(
            dados["menus"]
        ),
        "max_botoes": MAX_BOTOES,
        "views_persistentes": bool(
            getattr(
                bot,
                "_views_menus_registradas",
                False
            )
        ),
        "usuarios_painel": len(
            carregar_usuarios()["usuarios"]
        ),
        "atualizacoes": {
            "canal_id": carregar_atualizacoes().get("canal_id", ""),
            "futuras_ativas": bool(
                (carregar_atualizacoes().get("futuras") or {}).get(
                    "mensagens_ids"
                )
            ),
            "historico_total": len(
                carregar_atualizacoes().get("historico", [])
            ),
        },
    }, 200


if __name__ == "__main__":
    thread_bot = threading.Thread(
        target=iniciar_bot,
        daemon=True
    )
    thread_bot.start()

    porta = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=porta,
        debug=False,
        use_reloader=False
    )
