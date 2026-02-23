# Security & Governance Guide
## Azure OpenAI Model Migration Security Best Practices

This guide covers security considerations for migrating live systems from GPT-4 to GPT-5 on Azure AI Foundry.

---

## 1. Data Protection

### 1.1 Customer Data Handling

```python
# PII Detection and Redaction
from azure.ai.textanalytics import TextAnalyticsClient

def redact_pii(text: str, client: TextAnalyticsClient) -> str:
    """Redact PII before sending to model"""
    result = client.recognize_pii_entities([text])[0]
    redacted = text
    for entity in sorted(result.entities, key=lambda x: x.offset, reverse=True):
        redacted = redacted[:entity.offset] + "[REDACTED]" + redacted[entity.offset + entity.length:]
    return redacted
```

### 1.2 Data Classification

| Data Type | Classification | Handling |
|-----------|---------------|----------|
| Account Numbers | Confidential | Tokenize/Hash |
| Phone Numbers | PII | Redact in logs |
| Conversation Content | Internal | Encrypt at rest |
| Model Outputs | Internal | Audit log |

### 1.3 Encryption Standards

```yaml
encryption:
  at_rest:
    standard: AES-256
    key_management: Azure Key Vault
  in_transit:
    standard: TLS 1.3
    certificate: Azure-managed or custom
```

---

## 2. Azure Sandbox Tools Security

### 2.1 Code Interpreter

**When to Use:**
- Exact string matching
- Mathematical calculations
- Data format validation
- Pattern extraction

**Security Configuration:**
```python
# Secure Code Interpreter usage
tools = [{
    "type": "code_interpreter"
}]

# Always validate outputs
def validate_code_interpreter_output(output: str) -> bool:
    """Validate Code Interpreter didn't expose sensitive data"""
    patterns = [
        r'\b\d{3}-\d{2}-\d{4}\b',  # SSN
        r'\b\d{16}\b',              # Credit card
    ]
    for pattern in patterns:
        if re.search(pattern, output):
            return False
    return True
```

**Limitations (Security Features):**
- No network access
- Ephemeral file storage
- Resource limits enforced
- Sandboxed execution

### 2.2 Function Calling Security

```python
# Define allowed functions explicitly
allowed_tools = [
    {
        "type": "function",
        "function": {
            "name": "get_account_status",
            "description": "Get account status by customer ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "pattern": "^[A-Z]{3}[0-9]{6}$"  # Validate format
                    }
                },
                "required": ["customer_id"]
            }
        }
    }
]

# Validate tool calls before execution
def validate_tool_call(tool_call: dict) -> bool:
    """Validate tool call before execution"""
    if tool_call["function"]["name"] not in [t["function"]["name"] for t in allowed_tools]:
        return False
    # Additional validation...
    return True
```

---

## 3. MCP Server Security

### 3.1 Authentication & Authorization

```python
# MCP Server security configuration
mcp_security_config = {
    "authentication": {
        "method": "oauth2",
        "provider": "azure_ad",
        "token_validation": True,
        "required_scopes": ["mcp.read", "mcp.execute"]
    },
    "authorization": {
        "method": "rbac",
        "roles": {
            "agent": ["read", "execute_tools"],
            "admin": ["read", "write", "execute_tools", "manage"]
        }
    },
    "network": {
        "allowed_origins": ["https://your-app.azurewebsites.net"],
        "rate_limiting": {
            "requests_per_minute": 100,
            "burst_limit": 20
        }
    }
}
```

### 3.2 Prompt Injection Protection

```python
# Prompt injection detection
INJECTION_PATTERNS = [
    r"ignore previous instructions",
    r"disregard.*system prompt",
    r"you are now",
    r"new instructions:",
    r"override.*rules"
]

def detect_prompt_injection(user_input: str) -> bool:
    """Detect potential prompt injection attempts"""
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, user_input.lower()):
            return True
    return False

# Use in request handler
def handle_request(user_input: str):
    if detect_prompt_injection(user_input):
        log_security_event("prompt_injection_attempt", user_input)
        return {"error": "Invalid input detected"}
    # Continue processing...
```

### 3.3 Secure Tool Execution

```python
# Principle of least privilege for tools
class SecureToolExecutor:
    def __init__(self, allowed_tools: list, user_role: str):
        self.allowed_tools = allowed_tools
        self.user_role = user_role
        
    def execute(self, tool_name: str, params: dict) -> dict:
        # Validate tool is allowed
        if tool_name not in self.allowed_tools:
            raise SecurityError(f"Tool {tool_name} not allowed")
            
        # Validate parameters
        if not self._validate_params(tool_name, params):
            raise SecurityError("Invalid parameters")
            
        # Execute with logging
        self._log_execution(tool_name, params)
        return self._execute_tool(tool_name, params)
```

---

## 4. Customer Identification Methods

### 4.1 Comparison Matrix

| Method | Security Level | UX | Legal | Cost | Recommendation |
|--------|---------------|-----|-------|------|----------------|
| Passkey/FIDO2 | ★★★★★ | ★★★★ | ✅ | Low | **Primary** |
| Voice Biometrics | ★★★★ | ★★★★★ | ⚠️ | High | Secondary |
| SMS OTP | ★★★ | ★★★ | ✅ | Medium | Fallback |
| Knowledge-based | ★★ | ★★ | ✅ | Low | Avoid |
| PIN | ★★ | ★★★ | ✅ | Low | Legacy |

### 4.2 Passkey Implementation

```python
# Passkey/WebAuthn for voice agents
class PasskeyAuthenticator:
    """
    Implement Passkey authentication for service agents.
    Uses device biometrics + cryptographic proof.
    """
    
    async def initiate_auth(self, customer_id: str) -> dict:
        """Send authentication challenge to customer device"""
        challenge = self._generate_challenge()
        
        # Store challenge for verification
        await self._store_challenge(customer_id, challenge)
        
        # Send push notification to registered device
        await self._send_push_notification(
            customer_id,
            "Verify your identity for customer service",
            challenge
        )
        
        return {"status": "pending", "timeout": 60}
        
    async def verify_response(self, customer_id: str, response: dict) -> bool:
        """Verify cryptographic response from device"""
        stored_challenge = await self._get_challenge(customer_id)
        
        # Verify signature using stored public key
        public_key = await self._get_customer_public_key(customer_id)
        
        return self._verify_signature(
            stored_challenge,
            response["signature"],
            public_key
        )
```

### 4.3 Voice Biometrics (With Consent)

```python
# Voice biometrics with explicit consent
class VoiceBiometrics:
    def __init__(self):
        self.consent_required = True
        
    async def authenticate(self, audio_stream, customer_id: str) -> dict:
        # Check consent
        if not await self._has_consent(customer_id):
            return await self._request_consent(customer_id)
            
        # Extract voice print
        voice_print = await self._extract_voice_print(audio_stream)
        
        # Compare with stored print
        stored_print = await self._get_stored_print(customer_id)
        similarity = self._compare_prints(voice_print, stored_print)
        
        return {
            "authenticated": similarity > 0.85,
            "confidence": similarity,
            "method": "voice_biometrics"
        }
```

---

## 5. Audit & Compliance

### 5.1 Logging Requirements

```python
# Comprehensive audit logging
import structlog

logger = structlog.get_logger()

def log_model_interaction(
    request_id: str,
    customer_id: str,
    model: str,
    prompt_hash: str,  # Hash, not content
    response_category: str,
    latency_ms: int,
    tokens_used: int
):
    logger.info(
        "model_interaction",
        request_id=request_id,
        customer_id=hash_customer_id(customer_id),  # Hashed
        model=model,
        prompt_hash=prompt_hash,
        response_category=response_category,
        latency_ms=latency_ms,
        tokens_used=tokens_used,
        timestamp=datetime.utcnow().isoformat()
    )
```

### 5.2 Compliance Checklist

**GDPR:**
- [ ] Data minimization in prompts
- [ ] Right to deletion implementation
- [ ] Consent management
- [ ] Data processing documentation

**SOC 2:**
- [ ] Access controls documented
- [ ] Encryption at rest and in transit
- [ ] Audit logging enabled
- [ ] Incident response procedures

**Industry-Specific (TELCO):**
- [ ] Call recording consent
- [ ] Customer data protection
- [ ] Service availability SLAs
- [ ] Regulatory reporting

---

## 6. Incident Response

### 6.1 Security Event Categories

| Event Type | Severity | Response |
|------------|----------|----------|
| Prompt injection attempt | High | Block, log, alert |
| PII exposure | Critical | Block, purge, report |
| Unauthorized tool access | High | Block, revoke, investigate |
| Rate limit exceeded | Medium | Throttle, log |
| Authentication failure | Low-Medium | Log, track patterns |

### 6.2 Response Procedures

```python
class SecurityIncidentHandler:
    def handle_incident(self, event_type: str, details: dict):
        severity = self._get_severity(event_type)
        
        # Log incident
        self._log_incident(event_type, details, severity)
        
        # Immediate actions
        if severity == "critical":
            self._block_session(details["session_id"])
            self._alert_security_team(event_type, details)
            self._initiate_investigation(details)
            
        elif severity == "high":
            self._block_session(details["session_id"])
            self._alert_on_call(event_type, details)
            
        # All incidents
        self._update_metrics(event_type, severity)
```

---

## 7. Network Security

### 7.1 Azure OpenAI Network Configuration

```yaml
# Recommended network configuration
network_security:
  # Private endpoints for Azure OpenAI
  private_endpoint:
    enabled: true
    subnet: "/subscriptions/xxx/resourceGroups/xxx/providers/Microsoft.Network/virtualNetworks/xxx/subnets/ai-subnet"
    
  # Network Security Group rules
  nsg_rules:
    - name: "allow_https_inbound"
      priority: 100
      direction: "Inbound"
      access: "Allow"
      protocol: "Tcp"
      destination_port: 443
      source: "VirtualNetwork"
      
    - name: "deny_all_inbound"
      priority: 4096
      direction: "Inbound"
      access: "Deny"
      protocol: "*"
      source: "*"
      
  # IP restrictions
  ip_restrictions:
    allowed_ips:
      - "10.0.0.0/8"  # Internal only
```

### 7.2 API Key Management

```python
# Use Azure Key Vault for API keys
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

class SecureKeyManager:
    def __init__(self, vault_url: str):
        credential = DefaultAzureCredential()
        self.client = SecretClient(vault_url=vault_url, credential=credential)
        self._cache = {}
        self._cache_ttl = 3600  # Refresh hourly
        
    def get_api_key(self, key_name: str) -> str:
        """Get API key from Key Vault with caching"""
        if key_name in self._cache:
            cached, timestamp = self._cache[key_name]
            if time.time() - timestamp < self._cache_ttl:
                return cached
                
        secret = self.client.get_secret(key_name)
        self._cache[key_name] = (secret.value, time.time())
        return secret.value
```

---

## 8. Best Practices Summary

### Do's ✅

1. Use Azure AD authentication for all services
2. Implement private endpoints for Azure OpenAI
3. Encrypt all data at rest and in transit
4. Log all model interactions (without PII)
5. Validate all tool calls before execution
6. Use Passkey/FIDO2 for customer authentication
7. Implement rate limiting and throttling
8. Regular security audits and penetration testing

### Don'ts ❌

1. Never log full prompts or responses with PII
2. Never expose API keys in code or logs
3. Never allow unrestricted tool execution
4. Never skip input validation
5. Never use knowledge-based authentication alone
6. Never ignore rate limit warnings
7. Never deploy without security review

---

## 9. Deployment & Container Security

### 9.1 Docker Image Hardening

The framework ships a `Dockerfile` based on **Python 3.13-slim**.  Security considerations:

| Measure | Implementation |
|---------|----------------|
| **Base image** | `python:3.13-slim` — minimal attack surface |
| **Non-root user** | Application runs as non-root inside the container |
| **No secrets in image** | Credentials injected at runtime via `--env-file` or Azure Container Apps secrets (`secretRef`) |
| **Azure CLI inside container** | Required only for `DefaultAzureCredential` / Foundry integration |

### 9.2 Azure Container Apps Secrets

When deployed via `deploy.ps1`, sensitive values are stored as **Container Apps secrets** and injected through `secretRef` — they never appear in plain text in the YAML configuration or container logs.

```yaml
# Secrets flow
.env  ─→  deploy.ps1  ─→  Container Apps secrets  ─→  env vars inside container
```

### 9.3 Service Principal for Foundry

The deployment script automatically creates a Service Principal (`sp-model-migration-eval`) scoped to the Foundry project.  Credentials (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`) are written to `.env` and injected as container secrets.

### 9.4 Web UI Security

The Fluent 2 web interface serves only an **internal evaluation tool** — it is not designed for public-facing traffic.  Recommendations:

- Deploy behind **Azure Front Door** or an **Application Gateway** with WAF if external access is needed.
- Enable **IP restrictions** on the Container App to limit access to your corporate network.
- Use **Azure AD Easy Auth** for identity-based access control when exposing the app outside your team.

---

*Security guide version: 1.1 | Last updated: June 2025*
