
# INSTALAR:
# py -m pip install telethon

import json
import re
import time
import unicodedata
import urllib.request
from pathlib import Path

from telethon import TelegramClient, events

# =====================================
# CONFIGURACION TELEGRAM
# =====================================

API_ID = 34501633
API_HASH = "cc60b8ad4b4e95ab0908e6ef379efc49"
BASE = Path(__file__).resolve().parent

client = TelegramClient(
    str(BASE / "loschuckys_sesion"),
    API_ID,
    API_HASH
)

# =====================================
# DATOS DE PAGO
# =====================================

QR_URL = "https://i.postimg.cc/sfccGwZ0/image.png"

QR_ARCHIVO = BASE / "qr_pago.png"

NUMERO_CUENTA = "09400141002543052450"

# Imagen opcional para responder al comprobante
IMAGEN_ASESOR = BASE / "asesor.png"

# Archivo para recordar compradores pendientes
ESTADO_ARCHIVO = BASE / "estado_vip.json"

# Vigencia de una consulta: 7 dias
VIGENCIA = 7 * 24 * 60 * 60


# =====================================
# GUARDAR Y CARGAR ESTADO
# =====================================

def cargar_estado():
    if not ESTADO_ARCHIVO.exists():
        return {}

    try:
        with open(
            ESTADO_ARCHIVO, "r", encoding="utf-8"
        ) as archivo:
            datos = json.load(archivo)

        return {
            str(k): float(v)
            for k, v in datos.items()
        }

    except (OSError, ValueError, TypeError):
        return {}


pendientes = cargar_estado()


def guardar_estado():
    with open(
        ESTADO_ARCHIVO, "w", encoding="utf-8"
    ) as archivo:
        json.dump(pendientes, archivo)


def consulta_pendiente(usuario):
    fecha = pendientes.get(str(usuario))

    if not fecha:
        return False

    if time.time() - fecha > VIGENCIA:
        pendientes.pop(str(usuario), None)
        guardar_estado()
        return False

    return True


# =====================================
# PREPARAR IMAGEN QR
# =====================================

def preparar_qr():
    if QR_ARCHIVO.exists():
        print("QR encontrado")
        return

    print("Descargando QR...")

    solicitud = urllib.request.Request(
        QR_URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(
        solicitud,
        timeout=20
    ) as respuesta:
        QR_ARCHIVO.write_bytes(respuesta.read())

    print("QR descargado correctamente")


# =====================================
# DETECTAR MENSAJES DE COMPRA VIP
# =====================================

def normalizar(texto):
    texto = unicodedata.normalize(
        "NFD", texto.lower()
    )

    return "".join(
        letra for letra in texto
        if unicodedata.category(letra) != "Mn"
    )


def es_consulta_vip(texto):
    texto = normalizar(texto)

    plan = re.search(
        r"\b(vip|plan)\b",
        texto
    )

    compra = re.search(
        r"\b(comprar|compra|quiero|pagar|"
        r"pago|precio|cuanto|adquirir|"
        r"transferir)\b",
        texto
    )

    return bool(plan and compra)


# =====================================
# DETECTAR FOTOS
# =====================================

def es_imagen(event):
    if event.message.photo:
        return True

    archivo = event.message.file

    if archivo:
        tipo = archivo.mime_type or ""
        return tipo.startswith("image/")

    return False


# =====================================
# OBTENER DESTINATARIO
# =====================================

async def obtener_destinatario(event):
    try:
        destino = await event.get_input_sender()

        if destino is not None:
            return destino

    except (ValueError, TypeError):
        pass

    try:
        destino = await event.get_input_chat()

        if destino is not None:
            return destino

    except (ValueError, TypeError):
        pass

    await client.get_dialogs()

    try:
        return await event.get_input_sender()

    except (ValueError, TypeError):
        return None


# =====================================
# MENSAJE 1: ENVIAR QR
# =====================================

async def enviar_qr(destinatario):

    await client.send_file(
        destinatario,
        str(QR_ARCHIVO)
    )

    print("QR enviado")


# =====================================
# MENSAJE 2: TRANSFERENCIA
# =====================================

async def enviar_cuenta(destinatario):

    texto = (
        "🏦 <b>TRANSFERENCIA BANCARIA</b>\n\n"

        "Si eres BCP o utilizas otro banco, "
        "puedes consultar la transferencia "
        "con este número:\n\n"

        f"<pre>{NUMERO_CUENTA}</pre>\n\n"

        "📋 Copia el número para utilizarlo "
        "en tu aplicación bancaria.\n\n"

        "✅ Verifica el banco y el titular "
        "antes de realizar la transferencia."
    )

    await client.send_message(
        destinatario,
        texto,
        parse_mode="html"
    )

    print("Datos de transferencia enviados")


# =====================================
# RESPUESTA AL RECIBIR COMPROBANTE
# =====================================

async def recibir_comprobante(destinatario):

    # Enviar imagen opcional del asesor
    if IMAGEN_ASESOR.exists():

        await client.send_file(
            destinatario,
            str(IMAGEN_ASESOR)
        )

    # Enviar texto independiente
    texto = (
        "👑 <b>LOS CHUCKYS — VIP</b>\n\n"

        "✅ <b>¡Imagen recibida correctamente!</b>\n\n"

        "📩 Hemos recibido tu comprobante.\n\n"

        "⏳ Un asesor se comunicará contigo "
        "lo antes posible para revisar tu pago "
        "y coordinar la entrega de tu producto.\n\n"

        "💜 Gracias por tu confianza."
    )

    await client.send_message(
        destinatario,
        texto,
        parse_mode="html"
    )

    print("Respuesta de comprobante enviada")


# =====================================
# RESPUESTA AUTOMATICA PRINCIPAL
# =====================================

@client.on(events.NewMessage(incoming=True))
async def respuesta_automatica(event):

    # Solo mensajes privados
    if not event.is_private:
        return

    usuario = event.sender_id

    if usuario is None:
        return

    mensaje = event.raw_text or ""

    try:
        # Obtener destinatario sin usar solo ID
        destinatario = await obtener_destinatario(event)

        if destinatario is None:
            print("No se pudo reconocer destinatario")
            return

        # ==================================
        # CASO 1: RECIBEN UNA IMAGEN
        # ==================================

        if es_imagen(event):

            if consulta_pendiente(usuario):

                print("Comprobante recibido")

                await recibir_comprobante(
                    destinatario
                )

                # Evitar respuestas repetidas
                pendientes.pop(str(usuario), None)
                guardar_estado()

            return

        # ==================================
        # CASO 2: CONSULTAN UN PLAN VIP
        # ==================================

        if es_consulta_vip(mensaje):

            print("Nueva consulta VIP")

            # Primero el QR sin texto
            await enviar_qr(destinatario)

            # Segundo mensaje separado
            await enviar_cuenta(destinatario)

            # Esperar imagen del comprador
            pendientes[str(usuario)] = time.time()
            guardar_estado()

            print("Esperando comprobante...")

    except Exception as error:
        print(
            "Error en respuesta automatica:",
            error
        )


# =====================================
# INICIAR PROGRAMA
# =====================================

async def iniciar():

    if API_ID <= 0 or API_HASH == "TU_API_HASH":
        print("Configura API_ID y API_HASH")
        return

    preparar_qr()

    await client.start()

    # Cargar los chats conocidos
    await client.get_dialogs()

    usuario = await client.get_me()

    print("=" * 45)
    print("👑 LOS CHUCKYS — BOT VIP")
    print("=" * 45)
    print("Cuenta:", usuario.first_name)
    print("✅ Telegram conectado")
    print("✅ QR automatico activado")
    print("✅ Cuenta bancaria activada")
    print("✅ Deteccion de imagenes activada")
    print("✅ Respuesta de asesor activada")
    print("Esperando mensajes privados...")
    print("=" * 45)

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        with client:
            client.loop.run_until_complete(iniciar())

    except KeyboardInterrupt:
        print("Programa detenido")

    except Exception as error:
        print("Error al iniciar:", error)
