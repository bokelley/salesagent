# Adapters

Adapters connect the Prebid Sales Agent to ad servers. Choose the adapter that matches your ad server platform.

## Available Adapters

### [Google Ad Manager (GAM)](gam/)

Connect to Google Ad Manager to create and manage line items programmatically.

- Service account authentication
- Line item creation and management
- Creative trafficking
- Reporting integration

[Get started with GAM](gam/)

### [Mock Adapter](mock/)

A simulated ad server for testing and development.

- No external dependencies
- Simulates all AdCP operations
- Configurable delivery simulation
- Ideal for evaluation and testing

[Get started with Mock](mock/)

### [Triton Digital](triton/)

Connect to Triton Digital's TAP Media Buying API for streaming audio and podcast advertising.

- Publisher-scoped JWT authentication
- Campaign + flight creation against `mbapi.tritondigital.com`
- Station, station-group, genre, and daypart targeting
- CPM and FLAT_RATE pricing

[Get started with Triton](triton/)

### [FreeWheel](freewheel/)

Connect to Comcast/FreeWheel's Publisher API for video and CTV advertising.

- OAuth2 `client_credentials` authentication (7-day bearer token)
- Campaign + line item creation against `api.freewheel.tv`
- Placement, targeting profile, and custom key-value targeting
- CPM and FLAT_RATE pricing

[Get started with FreeWheel](freewheel/)

## Choosing an Adapter

| Adapter | Use Case |
|---------|----------|
| **GAM** | Production deployments with Google Ad Manager |
| **FreeWheel** | Video + CTV inventory via Comcast/FreeWheel Publisher API |
| **Triton** | Streaming audio + podcast inventory via TAP |
| **Mock** | Testing, demos, development |

## Multi-Tenant Considerations

In multi-tenant mode, each tenant can have their own adapter configuration:

- Different GAM network codes per tenant
- Mix of GAM and Mock adapters
- Per-tenant service accounts

See [Multi-Tenant Setup](../deployment/multi-tenant.md) for configuration details.

## Related Documentation

- [Adapter Architecture](../development/architecture.md#adapter-pattern) - How adapters work internally
- [Security](../security.md) - Adapter security boundaries
