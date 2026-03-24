# 🔄 User Data Backup & Restore

Herramienta PowerShell para descargar y restaurar todos los datos de un usuario (prompts, datos de test, topics, historial y resultados) desde cualquier instancia de la app — local, Docker o Azure Container Apps.

> **No requiere cambios en la app.** Usa exclusivamente las APIs REST existentes.

---

## Requisitos

- **PowerShell 5.1+** (incluido en Windows) o PowerShell 7+
- La app de destino debe estar **en ejecución** y accesible por HTTP/HTTPS
- Credenciales del usuario (email + OTP si `code_verification=true`)

---

## Backup (descarga)

### Uso básico

```powershell
# Servidor local (desarrollo)
.\tools\backup_user_data.ps1 -BaseUrl http://localhost:5000 -Email user@test.com

# Docker Desktop
.\tools\backup_user_data.ps1 -BaseUrl http://localhost:5000 -Email user@test.com

# Azure Container Apps
.\tools\backup_user_data.ps1 -BaseUrl https://ca-my-app-abc123.azurecontainerapps.io -Email user@test.com
```

### Opciones

| Parámetro | Descripción | Valor por defecto |
|-----------|-------------|-------------------|
| `-BaseUrl` | URL base de la app (obligatorio) | — |
| `-Email` | Email del usuario (obligatorio) | — |
| `-OtpCode` | Código OTP de 6 dígitos (si `code_verification=true`) | Interactivo |
| `-OutputDir` | Directorio donde guardar backups | `.\backups` |
| `-SkipResults` | No descargar resultados de evaluación (más rápido) | `false` |

### Ejemplo con OTP

Si la app tiene verificación de código activada, el script te pedirá el código. También puedes pasarlo directamente:

```powershell
# El código aparece en los logs del contenedor (docker logs <name>)
.\tools\backup_user_data.ps1 -BaseUrl http://localhost:5000 -Email user@test.com -OtpCode 847291
```

### Ejemplo sin resultados

Los resultados de evaluación pueden ser pesados. Si solo necesitas prompts y datos:

```powershell
.\tools\backup_user_data.ps1 -BaseUrl http://localhost:5000 -Email user@test.com -SkipResults
```

---

## Restore (restauración)

Sube los datos de un backup a otra instancia de la app, sobreescribiendo los prompts y datos del usuario.

### Uso básico

```powershell
# Primero: dry run (muestra qué se haría sin tocar nada)
.\tools\backup_user_data.ps1 -BaseUrl https://target-app.azurecontainerapps.io `
    -Email user@test.com `
    -Restore -BackupDir .\backups\user_at_test_com_20260314_153000 `
    -DryRun

# Ejecutar restore real
.\tools\backup_user_data.ps1 -BaseUrl https://target-app.azurecontainerapps.io `
    -Email user@test.com `
    -Restore -BackupDir .\backups\user_at_test_com_20260314_153000
```

### Opciones de restore

| Parámetro | Descripción |
|-----------|-------------|
| `-Restore` | Activa el modo restauración |
| `-BackupDir` | Ruta al directorio de backup a restaurar |
| `-DryRun` | Muestra qué se haría sin escribir nada |

### ¿Qué se restaura?

| Dato | ¿Se restaura? | Notas |
|------|:-:|-------|
| Prompts activos | ✅ | Sobreescribe los prompts de cada modelo |
| Datos sintéticos | ✅ | Sobreescribe los escenarios de test |
| Datos de topics archivados | ✅ | Restaura los datos de cada topic |
| Historial de versiones | ❌ | No hay API de escritura para el historial |
| Resultados de evaluación | ❌ | No hay API de escritura para resultados |

> **Nota:** Al restaurar prompts, la app crea automáticamente snapshots en el historial de versiones del usuario destino, así que el contenido anterior no se pierde.

---

## Estructura del backup

```
backups/user_at_test_com_20260314_153000/
├── manifest.json                          ← Metadatos del backup
├── prompts/
│   ├── gpt4/
│   │   ├── classification_agent_system.md
│   │   ├── dialog_agent_system.md
│   │   ├── rag_agent_system.md
│   │   └── tool_calling_agent_system.md
│   ├── gpt4o/
│   │   └── ...
│   ├── gpt5/
│   │   └── ...
│   └── ...
├── synthetic/
│   ├── classification/data.json
│   ├── dialog/data.json
│   ├── general/data.json
│   ├── rag/data.json
│   └── tool_calling/data.json
├── topics/
│   └── telco_customer_service/
│       ├── topic_meta.json
│       └── data/
│           ├── classification/data.json
│           ├── dialog/data.json
│           └── ...
├── history/
│   ├── versions.json                      ← Índice de versiones
│   └── gpt4__rag_agent_system__20260310_120000.md
└── results/
    ├── gpt4_classification_2026-03-10T12-00-00.json
    └── comparison_gpt4_vs_gpt5_rag_20260310_130000.json
```

### manifest.json

Cada backup incluye un manifiesto con metadatos:

```json
{
  "user_id": "user_at_test_com",
  "email": "user@test.com",
  "timestamp": "2026-03-14T15:30:00+01:00",
  "base_url": "https://ca-my-app.azurecontainerapps.io",
  "version": "1.0",
  "completed": "2026-03-14T15:30:45+01:00",
  "counts": {
    "prompts": 32,
    "scenarios": 70,
    "topics": 2,
    "versions": 15,
    "results": 8
  }
}
```

---

## Flujo de autenticación

```
┌─────────┐     POST /api/auth/login      ┌─────────┐
│  Script  │ ─────────────────────────────→│   App   │
│          │                               │         │
│          │  code_verification=false       │         │
│          │◄──── { status: authenticated } │         │
│          │                               │         │
│          │  code_verification=true        │         │
│          │◄──── { status: code_sent }     │         │
│          │                               │         │
│          │     POST /api/auth/verify      │         │
│          │ ─────────────────────────────→│         │
│          │◄──── { status: authenticated } │         │
└─────────┘                               └─────────┘
       │
       │  Session cookie mantenida
       │  para todas las llamadas API
       ▼
   GET /api/prompts
   GET /api/data/raw/<type>
   GET /api/topics
   GET /api/results
   ...
```

---

## Casos de uso

### 1. Backup antes de reiniciar un Container App

Los Container Apps sin volumen persistente pierden los datos al reiniciar:

```powershell
.\tools\backup_user_data.ps1 `
    -BaseUrl https://ca-model-migration.azurecontainerapps.io `
    -Email angel@microsoft.com
```

### 2. Copiar datos entre entornos

Migrar los prompts optimizados de producción a un entorno de desarrollo:

```powershell
# Backup desde producción
.\tools\backup_user_data.ps1 `
    -BaseUrl https://prod-app.azurecontainerapps.io `
    -Email angel@microsoft.com -SkipResults

# Restore en desarrollo
.\tools\backup_user_data.ps1 `
    -BaseUrl http://localhost:5000 `
    -Email angel@microsoft.com `
    -Restore -BackupDir .\backups\angel_at_microsoft_com_20260314_153000
```

### 3. Preparar un workshop

Restaurar datos de demostración para todos los asistentes:

```powershell
$attendees = @("user1@contoso.com", "user2@contoso.com", "user3@contoso.com")
$baseUrl = "https://workshop-app.azurecontainerapps.io"
$backupDir = ".\backups\template_user_20260314_100000"

foreach ($email in $attendees) {
    Write-Host "`n=== Restoring for $email ===" -ForegroundColor Cyan
    .\tools\backup_user_data.ps1 `
        -BaseUrl $baseUrl -Email $email `
        -Restore -BackupDir $backupDir
}
```

### 4. Backup periódico con tarea programada

```powershell
# save as scheduled_backup.ps1
$date = Get-Date -Format "yyyyMMdd"
.\tools\backup_user_data.ps1 `
    -BaseUrl https://my-app.azurecontainerapps.io `
    -Email admin@company.com `
    -OutputDir "D:\backups\$date" `
    -SkipResults
```

---

## Solución de problemas

| Error | Causa | Solución |
|-------|-------|----------|
| `Cannot reach /api/health` | La app no está corriendo | Verificar URL, puerto y que el contenedor esté activo |
| `HTTP 401` en login | Email no registrado o OTP incorrecto | Verificar email; revisar logs para el código OTP |
| `HTTP 404` en prompts | El modelo no tiene prompts creados | Normal para modelos recién añadidos — se salta |
| `OTP verification failed` | Código expirado (TTL: 5 min) | Ejecutar de nuevo; el script pedirá nuevo código |
| Backup vacío | Usuario nuevo sin datos | Generar datos desde la UI antes del backup |

---

## APIs utilizadas

| Endpoint | Método | Uso |
|----------|--------|-----|
| `/api/health` | GET | Verificar conectividad |
| `/api/auth/login` | POST | Iniciar sesión |
| `/api/auth/verify` | POST | Verificar código OTP |
| `/api/auth/me` | GET | Confirmar autenticación |
| `/api/prompts` | GET | Listar todos los prompts |
| `/api/prompts/<model>/<type>` | GET | Leer contenido de un prompt |
| `/api/prompts/<model>/<type>` | PUT | Restaurar un prompt |
| `/api/data/raw/<type>` | GET | Leer datos sintéticos |
| `/api/data/raw/<type>` | PUT | Restaurar datos sintéticos |
| `/api/data/raw/<type>?topic=<slug>` | GET | Leer datos de un topic |
| `/api/topics` | GET | Listar topics archivados |
| `/api/prompts/history` | GET | Listar historial de versiones |
| `/api/prompts/history/<id>` | GET | Leer contenido de una versión |
| `/api/results` | GET | Listar resultados |
| `/api/results/<filename>` | GET | Leer un resultado completo |
