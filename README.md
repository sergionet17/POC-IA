# WhatsApp Webhook PoC (FastAPI + Cloud Run)

Este repositorio contiene un webhook mínimo para WhatsApp Cloud API, listo para desplegar en **Google Cloud Run**.

## 1) Pre-requisitos
- Cuenta en GCP y **proyecto** creado (ej: `wa-poc-restaurante`).
- **gcloud** instalado y autenticado:
  ```bash
  gcloud auth login
  gcloud config set project wa-poc-restaurante
  ```

## 2) Variables necesarias
- `VERIFY_TOKEN`: token que usarás en Meta para verificar el webhook (ej: `mipoc123`).
- `PHONE_ID`: *Phone number ID* de tu número (desde WhatsApp Cloud API).
- `WA_TOKEN`: token de acceso (temporal o permanente) de WhatsApp Cloud API.

## 3) Construir la imagen (Cloud Build)
```bash
gcloud builds submit --tag gcr.io/wa-poc-restaurante/wa-webhook
```

## 4) Desplegar en Cloud Run
```bash
gcloud run deploy wa-webhook   --image gcr.io/wa-poc-restaurante/wa-webhook   --platform managed   --region southamerica-east1   --allow-unauthenticated   --set-env-vars VERIFY_TOKEN=mipoc123,PHONE_ID=716079928258789,WA_TOKEN=TU_TOKEN
```
> Sustituye `wa-poc-restaurante` por tu **ID de proyecto**, `716079928258789` por tu **PHONE_ID**, y `TU_TOKEN` por tu token **vigente**.

## 5) Configurar el webhook en Meta
- Copia la **URL HTTPS** que devuelve Cloud Run (algo como `https://wa-webhook-xxxxxx-uc.a.run.app`).
- En **Meta Developers → WhatsApp → Webhooks**:
  - **Callback URL**: `https://TU_URL/webhook`
  - **Verify token**: `mipoc123` (o el que definas)
  - Clic en **Verify and Save**
  - Suscribe el campo **messages**

## 6) Probar
Envía un mensaje desde tu WhatsApp al **número de prueba**. Deberías recibir: `Recibido ✅: <tu mensaje>`.

## 7) Notas
- Mensajes **libres** sólo se entregan dentro de la **ventana de 24h** desde el último mensaje del usuario, o si estás en sandbox con el destinatario aprobado.
- Fuera de la ventana, usa **plantillas** aprobadas.
