# Xiaoshi SaaS

A multi-tenant application framework built with FastAPI, Vue 3 and TypeScript. Run a standalone SaaS or connect independently deployed products to a central control service.

Each product owns its database, session credentials and local roles. Central provisioning and SSO synchronize identity and application access; business data synchronization is product-specific.

The default Chinese brand is **小柿 SaaS**. Instances can override the title, logo, favicon and site/tenant branding without modifying framework code.

See the [main README](README.md) for setup, configuration and development instructions. Existing copyright notices and license terms are retained in [LICENSE](LICENSE).

### Shared tenant AI

Open the avatar menu → Configuration Center → Tenant AI. Tenant administrators configure shared models and feature bindings for their tenant within each instance. Existing business permissions still apply. Chat resolves personal model → tenant model → deployment default. See the [tenant AI guide](docs/framework/tenant-ai-config.md) for integration, encrypted keys and Redis persistence.
