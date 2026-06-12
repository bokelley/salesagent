# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.8.0](https://github.com/bokelley/salesagent/compare/v1.7.0...v1.8.0) (2026-06-12)


### Features

* **#352:** emit proposals[] on get_products when buying_mode=brief ([#361](https://github.com/bokelley/salesagent/issues/361)) ([8026c6c](https://github.com/bokelley/salesagent/commit/8026c6c9ec3e7d9c0765235cd3abbe389ad7e971))
* **aao:** live publisher partnership counts + drop tenant.house_domain ([#78](https://github.com/bokelley/salesagent/issues/78)) ([4e58dc9](https://github.com/bokelley/salesagent/commit/4e58dc929905f253f93a544c50bb159fa380b989))
* Account management, adcp 3.10 migration, and BDD test infrastructure ([#1170](https://github.com/bokelley/salesagent/issues/1170)) ([9f9a230](https://github.com/bokelley/salesagent/commit/9f9a230ee93d1e060febcf1ab93325cf68f59f2a))
* **adapter:** add preview paths for FreeWheel, Broadstreet, SpringServe ([#456](https://github.com/bokelley/salesagent/issues/456)) ([d0a14c9](https://github.com/bokelley/salesagent/commit/d0a14c993eee75ee1c331399f13fa40aa767499f))
* **adapters:** rebuild triton + scaffold freewheel; security/correctness fixes ([#127](https://github.com/bokelley/salesagent/issues/127)) ([ab32db8](https://github.com/bokelley/salesagent/commit/ab32db899ef9136e20f205f6a9a75f3d8fa63c57))
* **adapters:** role-gap 404s, unified vendor_fault, upstream_unavailable, remediation hints + ENCRYPTION_KEY startup check ([#488](https://github.com/bokelley/salesagent/issues/488)) ([f63c959](https://github.com/bokelley/salesagent/commit/f63c959b79d14a1a6ed93eaf8a7a0221fe38c3e9))
* **adapters:** shared sync orchestration + uniform contract ([#382](https://github.com/bokelley/salesagent/issues/382)) ([#411](https://github.com/bokelley/salesagent/issues/411)) ([521eb34](https://github.com/bokelley/salesagent/commit/521eb3406ecf1c650fdd6ea6f3bdc7eae7b42019))
* **adapters:** split ADAPTER_CONNECTION_FAILED into typed sub-codes ([#467](https://github.com/bokelley/salesagent/issues/467)) ([#469](https://github.com/bokelley/salesagent/issues/469)) ([f6e6f2d](https://github.com/bokelley/salesagent/commit/f6e6f2df32b954b2e1d3573668a443d40a8084ee))
* **adapters:** SpringServe (Magnite) integration — inventory, signals, reporting, live writes ([#508](https://github.com/bokelley/salesagent/issues/508)) ([e3ef361](https://github.com/bokelley/salesagent/commit/e3ef3618f7273b1aad00421833af7cfb2c9b4ed4))
* **adapter:** widen probes — FreeWheel inventory binding, Broadstreet, SpringServe ([#445](https://github.com/bokelley/salesagent/issues/445)) ([5de203d](https://github.com/bokelley/salesagent/commit/5de203d3ee1257d0e7102e71b4f6f2711a8553b3))
* add composition API product authoring ([57ed023](https://github.com/bokelley/salesagent/commit/57ed023bc3ff4033dbcf82765558fcb93fdd6a26))
* add embedded sync health contract ([#647](https://github.com/bokelley/salesagent/issues/647)) ([61fc290](https://github.com/bokelley/salesagent/commit/61fc290622a98a0d970404f5a5497e2a7b9af335))
* Add embedded wholesale product authoring API ([#616](https://github.com/bokelley/salesagent/issues/616)) ([bdc9213](https://github.com/bokelley/salesagent/commit/bdc9213ae0bd42c7be745f419cee396e54c15b10))
* Add expired proposal cleanup job ([2cf46c7](https://github.com/bokelley/salesagent/commit/2cf46c7c9f3074fb18e03ecc22df5f6e267111cd))
* add FreeWheel nightly forecast delivery fallback ([c8eda64](https://github.com/bokelley/salesagent/commit/c8eda6449ea658cec82f6ae72fb532c8018e50a7))
* add GAM advertiser ensure endpoint ([e9b6c47](https://github.com/bokelley/salesagent/commit/e9b6c47fbe25ead1160a55ecc47bfaf675f824cd))
* add GAM advertiser readiness routing ([#658](https://github.com/bokelley/salesagent/issues/658)) ([2ed5bf7](https://github.com/bokelley/salesagent/commit/2ed5bf79d44706389746f10abe46de1d117f8748))
* add GAM pricing availability sync ([#656](https://github.com/bokelley/salesagent/issues/656)) ([7e104c5](https://github.com/bokelley/salesagent/commit/7e104c52d9b1e15c24de894937f53653e0deeb60))
* Add OpenTelemetry distributed tracing ([#620](https://github.com/bokelley/salesagent/issues/620)) ([a5b495b](https://github.com/bokelley/salesagent/commit/a5b495b3b7c457001598bc1e6cfed060d842a4be))
* add sandbox trafficking mode ([#662](https://github.com/bokelley/salesagent/issues/662)) ([9da7d8c](https://github.com/bokelley/salesagent/commit/9da7d8ce5f92889ab9a59bfc5aa459dc9c19e139))
* Add Sentry error reporting ([#724](https://github.com/bokelley/salesagent/issues/724)) ([f17f96b](https://github.com/bokelley/salesagent/commit/f17f96bbb4ba2c5dd5349ba5296f4b827edf6197))
* Add server-owned sync schedulers ([#648](https://github.com/bokelley/salesagent/issues/648)) ([eceb072](https://github.com/bokelley/salesagent/commit/eceb072ff9bc43c0336f1b9363797e036b843ec1))
* add tree-first inventory picker ([f4f518c](https://github.com/bokelley/salesagent/commit/f4f518ca38d1a4c3723044be7cb2bfdd84092686))
* **admin-nav:** promote Products/Inventory/Signals to top nav + dev-auth fixes ([#453](https://github.com/bokelley/salesagent/issues/453)) ([16663bc](https://github.com/bokelley/salesagent/commit/16663bc4e2cf399035a255e2a6d1aeced381192e))
* **admin:** add ALLOW_SIGNUPS env var to close self-service registration ([#418](https://github.com/bokelley/salesagent/issues/418)) ([5444009](https://github.com/bokelley/salesagent/commit/5444009830843fda35455cd30e7b231b906ec925))
* **admin:** hide Buyer Agents tab on embedded; rename Settings → Tenant Settings ([#420](https://github.com/bokelley/salesagent/issues/420)) ([70041ee](https://github.com/bokelley/salesagent/commit/70041eed715c4b51e9decdd0f0a59f94402d10b2))
* **admin:** promote Integrations to standalone Configure peer page (Sprint 7 Phase 2) ([#435](https://github.com/bokelley/salesagent/issues/435)) ([4c23529](https://github.com/bokelley/salesagent/commit/4c2352982e2b582baeadd193b3f6814a70bce2ed))
* **admin:** promote Policies & Workflows to standalone Configure peer page (Sprint 7 Phase 2) ([#434](https://github.com/bokelley/salesagent/issues/434)) ([b99d22a](https://github.com/bokelley/salesagent/commit/b99d22a6c96e26ba15a78face96397c5eb9ba475))
* **admin:** promote Publishers to standalone Configure peer page (Sprint 7 Phase 2) ([#431](https://github.com/bokelley/salesagent/issues/431)) ([406b251](https://github.com/bokelley/salesagent/commit/406b25111980508cdcd81b4fb4f7ce3c580522d9))
* **admin:** promote Signing Keys to standalone Configure peer page (Sprint 7 Phase 2) ([#433](https://github.com/bokelley/salesagent/issues/433)) ([b33e7ca](https://github.com/bokelley/salesagent/commit/b33e7ca313345e76dc76dd5646ee7d85e735f189))
* **admin:** remove in-page Products + Inventory tabs (Sprint 7 Phase 3) ([#470](https://github.com/bokelley/salesagent/issues/470)) ([af15ea7](https://github.com/bokelley/salesagent/commit/af15ea76de907df226b9df8272029cc182560477))
* **admin:** saToast() + saFetchAction() helpers for AJAX feedback ([#270](https://github.com/bokelley/salesagent/issues/270)) ([beaafd5](https://github.com/bokelley/salesagent/commit/beaafd5a669222a5d6622f6d5179ab5bb0671b54))
* **admin:** Sprint 7 IA refinements — inventory_sync flag, Publishers placement, Webhooks hide ([#473](https://github.com/bokelley/salesagent/issues/473)) ([#487](https://github.com/bokelley/salesagent/issues/487)) ([8077749](https://github.com/bokelley/salesagent/commit/8077749fe3d413f122c0482644000d1760803b8c))
* **admin:** stamp version + git SHA onto admin UI footer ([#345](https://github.com/bokelley/salesagent/issues/345)) ([552462c](https://github.com/bokelley/salesagent/commit/552462ca100b4570d18c1420d3c7609f723a9613))
* **admin:** sync publisher partners from AAO directory inverse-lookup ([#570](https://github.com/bokelley/salesagent/issues/570)) ([17abdad](https://github.com/bokelley/salesagent/commit/17abdadb931313d699157315ecd79d18f4cb72aa))
* adopt adcp 5.0 — delete obsolete middleware + migrate to public APIs ([#329](https://github.com/bokelley/salesagent/issues/329)) ([e6ecd0d](https://github.com/bokelley/salesagent/commit/e6ecd0de8b0464ae31a5855b201ade30590070aa))
* **audit:** record before/after on Principal.billing_enabled flips ([#247](https://github.com/bokelley/salesagent/issues/247)) ([c7337c1](https://github.com/bokelley/salesagent/commit/c7337c1252452a4787d34f06edc5d5c2ff8e3dd2))
* **auth:** gate MCP discovery tools at transport layer (adcp 5.6.0) ([#515](https://github.com/bokelley/salesagent/issues/515)) ([807ad43](https://github.com/bokelley/salesagent/commit/807ad4373e5344c83f8967483c41b05ea329af20))
* **auth:** land 4 follow-ups from PR [#194](https://github.com/bokelley/salesagent/issues/194) expert review ([#199](https://github.com/bokelley/salesagent/issues/199)) ([d16291a](https://github.com/bokelley/salesagent/commit/d16291a0cbb683356f7dd5fb8cef8d853abfa394))
* **billing:** per-buyer-agent billing_enabled gate ([#102](https://github.com/bokelley/salesagent/issues/102)) ([365f765](https://github.com/bokelley/salesagent/commit/365f765a853ed33ddf2b53033d766431d5c9f70c))
* **buyer-protocol:** accept X-Identity-* / X-Principal-Id from trusted network for embedded tenants ([af57efa](https://github.com/bokelley/salesagent/commit/af57efad08b8f18a13fb0196bea6a617ebf22076))
* **buyer-protocol:** accept X-Identity-* / X-Principal-Id from trusted network for embedded tenants ([2f39a58](https://github.com/bokelley/salesagent/commit/2f39a58eb3804c5bb3a69d20bb130ed70eae4385))
* **capabilities:** advertise agent billing in supported_billing ([#243](https://github.com/bokelley/salesagent/issues/243)) ([64ac04e](https://github.com/bokelley/salesagent/commit/64ac04e60ab1c945be87575a6eafc5102e9d2410))
* **capabilities:** publisher_domains portfolio + CI modernization ([#520](https://github.com/bokelley/salesagent/issues/520)) ([a60f56b](https://github.com/bokelley/salesagent/commit/a60f56becb6e44e2f1066207a71949858b5c2e9b))
* consolidate security-sensitive code — SSRF protection and OAuth normalization ([#1125](https://github.com/bokelley/salesagent/issues/1125)) ([60999d2](https://github.com/bokelley/salesagent/commit/60999d22e07d74ee1c305c2ba9b1bad7c0ef6ef4))
* **create_media_buy:** pre-flight overbook detection against GAM forecast ([#242](https://github.com/bokelley/salesagent/issues/242)) ([355e3ce](https://github.com/bokelley/salesagent/commit/355e3ce1c74b89deb45b8c4c36a3dd90740edc51)), closes [#152](https://github.com/bokelley/salesagent/issues/152)
* **creatives:** DeprecationWarning for legacy format_id wire shapes + provenance round-trip ([#292](https://github.com/bokelley/salesagent/issues/292)) ([17ace0e](https://github.com/bokelley/salesagent/commit/17ace0ef9c5cd7c618b98209051acc5ce173e25a))
* **creatives:** pre-approval gate — hold pending_review creatives back from the ad server ([#249](https://github.com/bokelley/salesagent/issues/249)) ([644c96e](https://github.com/bokelley/salesagent/commit/644c96e6f44c7e093e1b18e64050f890a2837df5)), closes [#145](https://github.com/bokelley/salesagent/issues/145)
* **dashboard:** Job 1 inventory coverage analytics ([#485](https://github.com/bokelley/salesagent/issues/485) PR-1) ([#497](https://github.com/bokelley/salesagent/issues/497)) ([0fa30ae](https://github.com/bokelley/salesagent/commit/0fa30ae1ba94a94f39f5822a831c864ee312ab04))
* **dashboard:** Ledger redesign — masthead + Incoming/Running/Pipeline ([#24](https://github.com/bokelley/salesagent/issues/24)) ([fa33c83](https://github.com/bokelley/salesagent/commit/fa33c83c38fcaf08ef853da8279bcf068f392cf9))
* **dashboard:** three-job seller workbench ([#471](https://github.com/bokelley/salesagent/issues/471)) ([#495](https://github.com/bokelley/salesagent/issues/495)) ([7e2b39f](https://github.com/bokelley/salesagent/commit/7e2b39fde6a4f61360782cfedf8c32c0d7ca0077))
* **dev:** DEV_TENANT_SUBDOMAINS env override for FastMCP allowlist ([#38](https://github.com/bokelley/salesagent/issues/38)) ([2c80d3b](https://github.com/bokelley/salesagent/commit/2c80d3b00dddae8d386dde2c4eea5e67b5298ce5))
* **dev:** seed default tenant for storyboard validation ([#254](https://github.com/bokelley/salesagent/issues/254)) ([b173d3f](https://github.com/bokelley/salesagent/commit/b173d3f930ce38168724a77d58272317dd689340))
* **embedded:** central mutation gate via require_tenant_access ([#63](https://github.com/bokelley/salesagent/issues/63)) ([841f329](https://github.com/bokelley/salesagent/commit/841f3292e2f62ee0b06b66b21dca9b0e64aa2b78))
* **embedded:** collapse Tenant Settings on embedded — locked page (Sprint 7 Phase 4d) ([#436](https://github.com/bokelley/salesagent/issues/436)) ([d09ed5c](https://github.com/bokelley/salesagent/commit/d09ed5cb3536091fa78fb2093abc5cbc074c2324))
* **embedded:** drop StatusPackagesBlock.last_24h_impressions ([#496](https://github.com/bokelley/salesagent/issues/496)) ([21de20b](https://github.com/bokelley/salesagent/commit/21de20b58a73b2c98b62a4f2c986e7bc22877d05))
* **embedded:** EMBEDDED_CAPABILITIES flag + Tenant Settings section gating (Sprint 7 Phase 4a+4b) ([#428](https://github.com/bokelley/salesagent/issues/428)) ([b9c3287](https://github.com/bokelley/salesagent/commit/b9c32879452f053ead9ff310876a9b94e6cbeddc))
* **embedded:** hard-hide signing keys + OIDC on embedded + review-driven fixes (Sprint 7 Phase 4c) ([#430](https://github.com/bokelley/salesagent/issues/430)) ([a92e67d](https://github.com/bokelley/salesagent/commit/a92e67d08cd36e1c735de5192dfa58cca41ce819))
* **embedded:** per-request preview + close mutation gate hole ([#59](https://github.com/bokelley/salesagent/issues/59)) ([b3d14a6](https://github.com/bokelley/salesagent/commit/b3d14a6915e2faf5d7194aef085a9949fcbd46fc))
* expose adapter runtime config API ([ec3df8f](https://github.com/bokelley/salesagent/commit/ec3df8f951a187a77af36ca399e4e6f1308d232a))
* expose adapter runtime config API ([2f36e86](https://github.com/bokelley/salesagent/commit/2f36e8690961618f8b350fe056d908ac980f84b1))
* Expose signal authoring capability metadata ([#716](https://github.com/bokelley/salesagent/issues/716)) ([736be77](https://github.com/bokelley/salesagent/commit/736be77d989115559963a86bd7f8c6f6c3f25444))
* FreeWheel API-Access client_credentials auth + sandbox support ([#703](https://github.com/bokelley/salesagent/issues/703)) ([516bcc2](https://github.com/bokelley/salesagent/commit/516bcc226935940c1900d126f3b79d608bd7d35b))
* **freewheel:** full Publisher API adapter — auth, inventory sync, targeting, formats ([#381](https://github.com/bokelley/salesagent/issues/381)) ([fc7dfde](https://github.com/bokelley/salesagent/commit/fc7dfde291e62ed588869672827c814951434536))
* **gam-projection:** provenance, audit log, race-safe materialization ([#193](https://github.com/bokelley/salesagent/issues/193)) ([6b4be9b](https://github.com/bokelley/salesagent/commit/6b4be9b1b1eaca943a40b81a6ebd65f12eddf7e9))
* integrate AdCP 3.1 beta support ([#610](https://github.com/bokelley/salesagent/issues/610)) ([541b54e](https://github.com/bokelley/salesagent/commit/541b54e31fcb4dd314047e1c7463180353a361c5))
* introduce BDD behavioral test suite (delivery metrics, creative formats) ([#1146](https://github.com/bokelley/salesagent/issues/1146)) ([67b8c7e](https://github.com/bokelley/salesagent/commit/67b8c7ef1dcda0bc9b02b230c21d873a853faeb8))
* **inventory-bundle:** adapter framework — protocol + GAM impl + FW/SS stubs (closes [#521](https://github.com/bokelley/salesagent/issues/521)) ([#544](https://github.com/bokelley/salesagent/issues/544)) ([2471486](https://github.com/bokelley/salesagent/commit/2471486673e90bf9fbf088c056965f35506dbf93))
* **inventory-bundle:** edit page redesign — sidebar, blast radius, sticky form bar ([#528](https://github.com/bokelley/salesagent/issues/528)) ([b486d69](https://github.com/bokelley/salesagent/commit/b486d697b9c1ad1643c41c985a7950d53a6c76f4))
* **inventory-bundle:** finish editor follow-up polish ([d90ab5d](https://github.com/bokelley/salesagent/commit/d90ab5db1e82769c9aa4fdb60bc74e1eaa695f0e))
* **inventory-bundle:** live sidebar validation ([#529](https://github.com/bokelley/salesagent/issues/529)) ([#537](https://github.com/bokelley/salesagent/issues/537)) ([7b10aee](https://github.com/bokelley/salesagent/commit/7b10aeeb842990247ca642ea4ff5d7d248f6f997))
* **inventory-bundle:** multi-domain publisher_properties editor ([#532](https://github.com/bokelley/salesagent/issues/532)) ([#539](https://github.com/bokelley/salesagent/issues/539)) ([6018df9](https://github.com/bokelley/salesagent/commit/6018df96c29ca34f0f13891b33de93ccf66850c8))
* **inventory-bundle:** per-chip Reuse action on editor inventory chips ([#542](https://github.com/bokelley/salesagent/issues/542)) ([#543](https://github.com/bokelley/salesagent/issues/543)) ([f0b2ff5](https://github.com/bokelley/salesagent/commit/f0b2ff5809019281b32bfa3597e2943452e3e426))
* **inventory-bundle:** property-tag typeahead + click-to-add chip picker ([#532](https://github.com/bokelley/salesagent/issues/532), partial) ([#535](https://github.com/bokelley/salesagent/issues/535)) ([b0b970d](https://github.com/bokelley/salesagent/commit/b0b970d0924e552587a6a8681d801aa109b84d1b))
* **inventory-bundle:** real Preview page — buyer's-eye view ([#531](https://github.com/bokelley/salesagent/issues/531)) ([#536](https://github.com/bokelley/salesagent/issues/536)) ([d4f46d5](https://github.com/bokelley/salesagent/commit/d4f46d5753818d734c00cb83b33da7b44a57b5f6))
* **inventory-bundle:** resolve external IDs to names + expand 'Used by' ([#530](https://github.com/bokelley/salesagent/issues/530)) ([#534](https://github.com/bokelley/salesagent/issues/534)) ([c545aef](https://github.com/bokelley/salesagent/commit/c545aef427778d0e3d805b2777b751c277274e50))
* **inventory-bundle:** reverse-add Reuse page (closes [#524](https://github.com/bokelley/salesagent/issues/524)) ([#541](https://github.com/bokelley/salesagent/issues/541)) ([91d0f9e](https://github.com/bokelley/salesagent/commit/91d0f9e7593be4e2b1712ba74663e01b742ce9ae))
* **inventory-bundle:** seed suggestions + reuse menu (list v2) ([#526](https://github.com/bokelley/salesagent/issues/526)) ([8b8d6b9](https://github.com/bokelley/salesagent/commit/8b8d6b95c40144fa10be042cda7d18b242bd3cfe))
* **inventory-bundle:** wire duplicate action — POST /&lt;id&gt;/duplicate ([#519](https://github.com/bokelley/salesagent/issues/519)) ([36b624c](https://github.com/bokelley/salesagent/commit/36b624cab010f1fd42ead16d2e3e71e6efea3ae7))
* **inventory:** redesign bundles list — coverage strip + multi-use rail ([#485](https://github.com/bokelley/salesagent/issues/485)) ([#513](https://github.com/bokelley/salesagent/issues/513)) ([bdaccc6](https://github.com/bokelley/salesagent/commit/bdaccc65a598bf0fcee564f4546f435a39f523bb))
* **media-buy:** real-GAM end-to-end lifecycle + auto-naming UX + delivery error fidelity ([#40](https://github.com/bokelley/salesagent/issues/40)) ([e683d83](https://github.com/bokelley/salesagent/commit/e683d8370b50c43d7a257fdcbebb73c7b0a6987e))
* migrate to adcp 3.12.0 (rc.3 spec alignment) ([#1217](https://github.com/bokelley/salesagent/issues/1217)) ([40ce493](https://github.com/bokelley/salesagent/commit/40ce4935ea763b7583a0470a60d256ef467abb24))
* **ops:** add tenant export/import for legacy → embedded migration ([#403](https://github.com/bokelley/salesagent/issues/403)) ([212e1e1](https://github.com/bokelley/salesagent/commit/212e1e1e79be47d16360f00cd577c18920658cd9))
* populate context, sandbox, and buyer-safe account on create_media_buy success (adcp b9) ([#711](https://github.com/bokelley/salesagent/issues/711)) ([585d397](https://github.com/bokelley/salesagent/commit/585d397d1dc1c54e02baa89281d1fb4b7d00baa6))
* project GAM orders into get_media_buys + materialize on update ([#136](https://github.com/bokelley/salesagent/issues/136)) ([232662c](https://github.com/bokelley/salesagent/commit/232662c6d41ca479713ad9524efe941d29ab2b79))
* **proposal:** adopt adcp 5.5.0 — framework derivation + PgProposalStore swap (supersedes [#419](https://github.com/bokelley/salesagent/issues/419)) ([#422](https://github.com/bokelley/salesagent/issues/422)) ([3c58544](https://github.com/bokelley/salesagent/commit/3c58544f19e7f784f13308e8cbffb55376a774cd))
* **proposal:** implement v1 refine_products + flip capabilities.refine=True ([#385](https://github.com/bokelley/salesagent/issues/385)) ([1d12612](https://github.com/bokelley/salesagent/commit/1d12612cc55ca6b9f966b3b0cac62569fcc820dc))
* **proposal:** wire Postgres-backed ProposalStore for create_media_buy(proposal_id=…) ([#390](https://github.com/bokelley/salesagent/issues/390)) ([0f4bcc4](https://github.com/bokelley/salesagent/commit/0f4bcc45221a75e4b89b5c724cb59345440ad7fb))
* publish adapter tenant-management contracts ([797145a](https://github.com/bokelley/salesagent/commit/797145a33cd4899cb5a2be2ed3372e9a4685c2b9))
* Publish adapter tenant-management contracts ([5a19b5d](https://github.com/bokelley/salesagent/commit/5a19b5de94731c1abf70f316b133260f493caf18))
* **rbac:** role enforcement on tenant-scoped admin routes (sprint 4) ([#112](https://github.com/bokelley/salesagent/issues/112)) ([d5cec4f](https://github.com/bokelley/salesagent/commit/d5cec4fc3e48bce0dfee1ae9defd18802cc8ea61))
* **repos:** write methods for tenant approval mode + creative approval (closes [#42](https://github.com/bokelley/salesagent/issues/42)) ([#66](https://github.com/bokelley/salesagent/issues/66)) ([8b3eaed](https://github.com/bokelley/salesagent/commit/8b3eaed479a1e294fabba0217b7a1ad1e13f6fdb))
* **scheduler:** heartbeat reports for pending_start + paused buys (closes [#48](https://github.com/bokelley/salesagent/issues/48)) ([#82](https://github.com/bokelley/salesagent/issues/82)) ([2908c27](https://github.com/bokelley/salesagent/commit/2908c2786b007c71a654c190a4478c3f0f90000d))
* **schemas:** introduce ResolvedProduct sidecar (Phase 2 slice 2) ([#186](https://github.com/bokelley/salesagent/issues/186)) ([b921b9c](https://github.com/bokelley/salesagent/commit/b921b9cc824112c6a02b893cfacb73c06c9e711b))
* **scripts:** typed adcp SDK validation in verify_embedded_mode.py ([#20](https://github.com/bokelley/salesagent/issues/20)) ([c0d193d](https://github.com/bokelley/salesagent/commit/c0d193d25a182f3ee3a607f6cd5d454f8b851450))
* **security:** app-wide CSRF defense for admin POSTs ([#248](https://github.com/bokelley/salesagent/issues/248)) ([153948d](https://github.com/bokelley/salesagent/commit/153948d5baa577d60815c4d21f1307b5fbed1c8f))
* **signals:** active-buy reference counts + typed-DELETE confirmation ([#482](https://github.com/bokelley/salesagent/issues/482)) ([1aaf751](https://github.com/bokelley/salesagent/commit/1aaf751f141edbe6aa742dcf16f68ebab32ae453))
* **signals:** ad-ops polish — search, delete warning, preview, cleanup ([#472](https://github.com/bokelley/salesagent/issues/472)) ([758efb6](https://github.com/bokelley/salesagent/commit/758efb6e135edfa47a493e277a09330f46c21cc2))
* **signals:** bulk-map UX — tick GAM entities, click Create ([#466](https://github.com/bokelley/salesagent/issues/466)) ([c4175ee](https://github.com/bokelley/salesagent/commit/c4175eea7bd7e55732c0483a2456a57413f4034c))
* **signals:** complex GAM targeting via embedded TargetingWidget ([#462](https://github.com/bokelley/salesagent/issues/462)) ([066a04a](https://github.com/bokelley/salesagent/commit/066a04a5560215d714a482f524a935eefcc677b6))
* **signals:** composite builder + 'Maps to' edit panel + bulk-map polish ([#468](https://github.com/bokelley/salesagent/issues/468)) ([3f5c4da](https://github.com/bokelley/salesagent/commit/3f5c4da07d3b6099bbf71a0328c2523210f8543d))
* **signals:** embedded composition API + TenantSignal-based adapter capability map ([#439](https://github.com/bokelley/salesagent/issues/439)) ([b391cf2](https://github.com/bokelley/salesagent/commit/b391cf27d0a59f097346737bab33935928815909))
* **signals:** persisted GAM targeting values + bulk-map polish ([#490](https://github.com/bokelley/salesagent/issues/490)) ([fce977f](https://github.com/bokelley/salesagent/commit/fce977fbaf293ed9019effbbb02d0e32a182ffd5)), closes [#479](https://github.com/bokelley/salesagent/issues/479)
* **signals:** source-centric Signals page redesign (Claude Design handoff) ([#498](https://github.com/bokelley/salesagent/issues/498)) ([c01095d](https://github.com/bokelley/salesagent/commit/c01095df7c0fa2ba5cd0e3514b7bab51d8c88fdd))
* **signals:** source-first authoring + auto-generated signal_id ([#458](https://github.com/bokelley/salesagent/issues/458)) ([64500bb](https://github.com/bokelley/salesagent/commit/64500bb47d2549ad2c7ee038324ff1c86f2471f2))
* **signals:** tags + bulk operations + edit-page polish ([#484](https://github.com/bokelley/salesagent/issues/484)) ([1099ff2](https://github.com/bokelley/salesagent/commit/1099ff210c0761ca10915caa5717d8a0f9538e9d)), closes [#477](https://github.com/bokelley/salesagent/issues/477) [#478](https://github.com/bokelley/salesagent/issues/478)
* **signals:** v2 design — split-row source→signal grid ([#504](https://github.com/bokelley/salesagent/issues/504)) ([dc9c144](https://github.com/bokelley/salesagent/commit/dc9c1447813ded6327e5bd6a7b95a52740b51e5c))
* **signing:** admin UI to generate / rotate-out webhook-signing keys ([#234](https://github.com/bokelley/salesagent/issues/234)) ([5655abb](https://github.com/bokelley/salesagent/commit/5655abbaa8b7394a34a89a46fcfe811c4899dd25))
* **signing:** per-buyer-agent trust + brand.json admit ([#70](https://github.com/bokelley/salesagent/issues/70)) ([766ab3a](https://github.com/bokelley/salesagent/commit/766ab3a6621ad57967878827cdb7f1d6f0d5f8e6))
* **signing:** RFC 9421 verifier for non-embedded buyer protocol — PRs 1+2A/B/C/D + security fixes ([#39](https://github.com/bokelley/salesagent/issues/39)) ([f55bef2](https://github.com/bokelley/salesagent/commit/f55bef279b8f8f95ed66e30117f526a4eaa78fa6))
* simplify bundles and add inventory capabilities ([00855a6](https://github.com/bokelley/salesagent/commit/00855a6a96224344fa3f8c575dd421c41c0f4030))
* **single-tenant:** derive SALES_AGENT_DOMAIN from tenant virtual_host ([#449](https://github.com/bokelley/salesagent/issues/449)) ([0cb6395](https://github.com/bokelley/salesagent/commit/0cb6395d01aa77fdfe67bce892b32a949fba2ce4))
* **single-tenant:** normalize virtual_host, log DB lookup failures, invalidate cache on edit ([#459](https://github.com/bokelley/salesagent/issues/459)) ([105809d](https://github.com/bokelley/salesagent/commit/105809dce33834e81cc97f8109e1fd92590ff154))
* **springserve:** add SpringServe (Magnite) ad-server adapter — direct CTV/OLV/audio integration for Talpa ([#427](https://github.com/bokelley/salesagent/issues/427)) ([e3c0f7e](https://github.com/bokelley/salesagent/commit/e3c0f7e9e8e2706b5c7c4501e75f4549a8fef752))
* support signal discovery catalog webhooks ([#561](https://github.com/bokelley/salesagent/issues/561)) ([72dd99d](https://github.com/bokelley/salesagent/commit/72dd99d7993b68bb44faa01742bad0e1f71397d9))
* Surface webhook signing capabilities ([#587](https://github.com/bokelley/salesagent/issues/587)) ([6ff53d4](https://github.com/bokelley/salesagent/commit/6ff53d4094c3d55c9652c4b798c8af02cfa668ab))
* **tenant-management:** expose initial_principal.access_token on provision response ([3a494f7](https://github.com/bokelley/salesagent/commit/3a494f75f5fcbf9aab54834748030ef3c6a69094))
* **tenant-management:** expose initial_principal.access_token on provision response ([a14846d](https://github.com/bokelley/salesagent/commit/a14846d7e94651b643481a5720ad22b159f68db6))
* universal request normalization for AdCP backward compatibility ([#1175](https://github.com/bokelley/salesagent/issues/1175)) ([343c9a8](https://github.com/bokelley/salesagent/commit/343c9a85ee7544b27462fc23cf557574a8274c31))
* Use canonical creative formats across adapters ([#619](https://github.com/bokelley/salesagent/issues/619)) ([fd94c57](https://github.com/bokelley/salesagent/commit/fd94c57491deaeccdd7bb7df6aa74bfdb4de9e5c))
* **webhooks:** emit media_buy.status_changed when GAM background approval completes ([#461](https://github.com/bokelley/salesagent/issues/461)) ([fcb5486](https://github.com/bokelley/salesagent/commit/fcb548613ba5de95744b5455e28c9685ee7d30f4))
* **webhooks:** expand event catalog — creative, principal, product ([#446](https://github.com/bokelley/salesagent/issues/446)) ([150bae0](https://github.com/bokelley/salesagent/commit/150bae03ccec809d3002b5acdbecabeb3a3f4417))
* **webhooks:** per-fire delivery visibility on get_media_buys ([#213](https://github.com/bokelley/salesagent/issues/213)) ([0595c54](https://github.com/bokelley/salesagent/commit/0595c544569173a7275332e6e92f1382564749f7))
* **webhooks:** wire creative.created and media_buy.created from agent flows ([#457](https://github.com/bokelley/salesagent/issues/457)) ([e10027b](https://github.com/bokelley/salesagent/commit/e10027b97c6a3302d0f9d97b00f28c11220a48a9))
* **webhooks:** wire sync.completed/sync.failed + 409 retry contract ([#463](https://github.com/bokelley/salesagent/issues/463)) ([#465](https://github.com/bokelley/salesagent/issues/465)) ([23947da](https://github.com/bokelley/salesagent/commit/23947da37843f6804f179be43097cd453ecf36ab))


### Bug Fixes

* **#336:** enable Add Publisher on embedded view + fix(scheduler): DetachedInstanceError ([#392](https://github.com/bokelley/salesagent/issues/392)) ([d560251](https://github.com/bokelley/salesagent/commit/d560251ce35f1fc9f258088e01d8040991f150bc))
* **#338:** use url_for and relabel button to "Advertisers" on webhooks page ([#367](https://github.com/bokelley/salesagent/issues/367)) ([aaeaaf4](https://github.com/bokelley/salesagent/commit/aaeaaf4e41b5cb91fa85e4994cbf38771ee6bf99))
* **#351:** raise PRODUCT_NOT_FOUND for nonexistent product_id ([#358](https://github.com/bokelley/salesagent/issues/358)) ([3c72b05](https://github.com/bokelley/salesagent/commit/3c72b05c676d92111c8f9bde6245c547228fee6f))
* **#353:** emit status on update_media_buy pause/resume/cancel responses ([#359](https://github.com/bokelley/salesagent/issues/359)) ([d9e56d6](https://github.com/bokelley/salesagent/commit/d9e56d67f04e761a609fdcef83a5de96b2ba1155))
* **#354:** resolve tenant_id from auth_info on list_accounts/sync_accounts ([#360](https://github.com/bokelley/salesagent/issues/360)) ([3b22afc](https://github.com/bokelley/salesagent/commit/3b22afc3417bb87aac04a361901aced618b451f8))
* **#355:** strip ctx/input/url from pydantic errors() in wire envelope ([#356](https://github.com/bokelley/salesagent/issues/356)) ([1197b87](https://github.com/bokelley/salesagent/commit/1197b87a8004fff92b84941dc8cb0eb355c7987c))
* **#357:** use url_for() for tenant admin links so embedded-mode mounts work ([#393](https://github.com/bokelley/salesagent/issues/393)) ([43a56e4](https://github.com/bokelley/salesagent/commit/43a56e4ab7fbed663617e79947f2543c848b284e))
* **#362:** hide AXE Set Key controls on embedded tenants ([#368](https://github.com/bokelley/salesagent/issues/368)) ([8692acb](https://github.com/bokelley/salesagent/commit/8692acbc583eae6f040df77d5694fcf4ddd30371))
* **#363:** unblock Policies & Workflows writes on embedded tenants ([#370](https://github.com/bokelley/salesagent/issues/370)) ([3ad8a7f](https://github.com/bokelley/salesagent/commit/3ad8a7fe7473a9a6d85a3659e949d132710b3229))
* **#364:** explain empty Allowed Principals dropdown on embedded tenants ([#371](https://github.com/bokelley/salesagent/issues/371)) ([d83f84a](https://github.com/bokelley/salesagent/commit/d83f84a5937a8db38e8e477fbe36ea89ae639b28))
* **#365:** allow AI/Logfire test-connection probes on embedded tenants ([#369](https://github.com/bokelley/salesagent/issues/369)) ([42ba8b6](https://github.com/bokelley/salesagent/commit/42ba8b675ea4f004ed2948bc87703b473e574def))
* **#374:** coerce update_media_buy status to wire enum at response boundary ([#375](https://github.com/bokelley/salesagent/issues/375)) ([64ce051](https://github.com/bokelley/salesagent/commit/64ce051035fc0740a606a665e8463a834e858c98))
* **#377:** four-state aao_status_kind + permissive unbound resolution ([#380](https://github.com/bokelley/salesagent/issues/380)) ([105279d](https://github.com/bokelley/salesagent/commit/105279d3f1a2851fca549aeecf6e92e65fe337bf)), closes [#377](https://github.com/bokelley/salesagent/issues/377)
* **a2a:** redirect /.well-known/agent.json to /agent-card.json (closes [#267](https://github.com/bokelley/salesagent/issues/267)) ([#269](https://github.com/bokelley/salesagent/issues/269)) ([3e3b5ed](https://github.com/bokelley/salesagent/commit/3e3b5ed6196e9073576889b25dd46ceedaa330cb))
* **a2a:** regression test for bearer-auth translation on the A2A leg ([#126](https://github.com/bokelley/salesagent/issues/126)) ([aef1a0d](https://github.com/bokelley/salesagent/commit/aef1a0d8e77d6f772d59dba538e6c39b2c161e08)), closes [#104](https://github.com/bokelley/salesagent/issues/104)
* **a2a:** rewrite agent-card URL fields with public host (closes [#103](https://github.com/bokelley/salesagent/issues/103)) ([#119](https://github.com/bokelley/salesagent/issues/119)) ([f7fbaf6](https://github.com/bokelley/salesagent/commit/f7fbaf6a5642f015d05e90738528b5c9df5d36eb))
* **a2a:** typed AdCPError subclasses translate to wire error codes on A2A path (closes [#319](https://github.com/bokelley/salesagent/issues/319)) ([#322](https://github.com/bokelley/salesagent/issues/322)) ([cf429a3](https://github.com/bokelley/salesagent/commit/cf429a3b60c1b6df375563b1cac0c0d5684cdb89))
* **aao:** IDN hostname fold + merge migration heads ([#135](https://github.com/bokelley/salesagent/issues/135)) ([fd22816](https://github.com/bokelley/salesagent/commit/fd228164faeaff5dbc5f76bed745c6251d03e1c5))
* accept embedded approval settings on provision ([#685](https://github.com/bokelley/salesagent/issues/685)) ([d1baf81](https://github.com/bokelley/salesagent/commit/d1baf81bec08bdd51c48c24a2fdda8c1d2470cae))
* accept storefront selector query aliases ([#718](https://github.com/bokelley/salesagent/issues/718)) ([ef6c1bf](https://github.com/bokelley/salesagent/commit/ef6c1bf8d4872bde0c22d8cfa83b24e9aec9735e))
* **accounts:** map pending_provision → pending_approval on wire (closes [#332](https://github.com/bokelley/salesagent/issues/332)) ([#333](https://github.com/bokelley/salesagent/issues/333)) ([920ec49](https://github.com/bokelley/salesagent/commit/920ec49088691a92bbe3ef79e0ae155a4e03bd29))
* **accounts:** sync_accounts returns stable account_id (closes storyboard sync_accounts assertion) ([#328](https://github.com/bokelley/salesagent/issues/328)) ([1d7d5bc](https://github.com/bokelley/salesagent/commit/1d7d5bc5d4404f925d24c1323f9bebd03f20445a))
* **adapters/springserve:** Talpa launch — Line Item class + correct wire shapes ([#512](https://github.com/bokelley/salesagent/issues/512)) ([b445957](https://github.com/bokelley/salesagent/commit/b4459578be323e61707c33c8a25ed176061a9614))
* **adcp:** VERSION_UNSUPPORTED, adcp_error envelope, capabilities /status ([#350](https://github.com/bokelley/salesagent/issues/350)) ([7b99b4b](https://github.com/bokelley/salesagent/commit/7b99b4be7a9694f0e08b65ac1a5359a27a26ac25))
* add MCP session guard configuration ([#678](https://github.com/bokelley/salesagent/issues/678)) ([78d38e6](https://github.com/bokelley/salesagent/commit/78d38e62fc4c0e7019f6705ae753a2433900cc9b))
* Add media buy revision concurrency controls ([#631](https://github.com/bokelley/salesagent/issues/631)) ([0c65552](https://github.com/bokelley/salesagent/commit/0c655528d093b4f61256aa46fce9c12e3a91145b))
* add missing AdCP spec fields to UpdateMediaBuyRequest and correct e2e assertions ([#1152](https://github.com/bokelley/salesagent/issues/1152)) ([757ed7e](https://github.com/bokelley/salesagent/commit/757ed7e7a2266fe9baba3e8944ced4429f393afe))
* Address creative sync and tenant API regressions ([#668](https://github.com/bokelley/salesagent/issues/668)) ([e9da072](https://github.com/bokelley/salesagent/commit/e9da072fa8ed9455e8053a3d34891c0768043926))
* address media buy E2E regressions ([#672](https://github.com/bokelley/salesagent/issues/672)) ([54c76bb](https://github.com/bokelley/salesagent/commit/54c76bb8284f9cb062e96c18035feb00643b4ed9))
* Address OTEL tracing review issues ([#621](https://github.com/bokelley/salesagent/issues/621)) ([f9f8388](https://github.com/bokelley/salesagent/commit/f9f8388a1304148b6aecdfbb9eafd34c9e128459))
* **admin-mount:** redirect apex sales-agent.&lt;domain&gt;/ to /signup ([#35](https://github.com/bokelley/salesagent/issues/35)) ([d1853be](https://github.com/bokelley/salesagent/commit/d1853becee17bcb69261028784551a7f6d4f7793))
* **admin-mount:** serve /robots.txt as public Disallow / instead of 401 from A2A ([#407](https://github.com/bokelley/salesagent/issues/407)) ([6db80f4](https://github.com/bokelley/salesagent/commit/6db80f4395faea5729b86a0e9b1fc656e5860f85))
* **admin:** edit inventory profile uses profile.format_ids, not profile.formats ([#510](https://github.com/bokelley/salesagent/issues/510)) ([922612c](https://github.com/bokelley/salesagent/commit/922612c7eef4d37101b903fc1723de9b24bda3b1))
* **admin:** exempt S2S API blueprints from cross-origin CSRF guard ([#423](https://github.com/bokelley/salesagent/issues/423)) ([3257883](https://github.com/bokelley/salesagent/commit/325788365c5e8ed028d28d90657c2fd925f6432b))
* **admin:** make workflow approvals reachable from the workflows page ([#223](https://github.com/bokelley/salesagent/issues/223)) ([5a38989](https://github.com/bokelley/salesagent/commit/5a3898943f5f8f7382279dd54d93308db4a17368)), closes [#142](https://github.com/bokelley/salesagent/issues/142) [#159](https://github.com/bokelley/salesagent/issues/159)
* **admin:** persistent highlight on creatives review page when arrived from buy detail ([#258](https://github.com/bokelley/salesagent/issues/258)) ([7901a6d](https://github.com/bokelley/salesagent/commit/7901a6d77f04f57e17576caf3de3ad57bb7af03d))
* **admin:** rename tenants.settings url_for to tenants.tenant_settings ([#154](https://github.com/bokelley/salesagent/issues/154)) ([#205](https://github.com/bokelley/salesagent/issues/205)) ([cc3b4e9](https://github.com/bokelley/salesagent/commit/cc3b4e943c25461a8a0f9b91430f2e29c5f459ad))
* **admin:** scope dashboard activity ledger to last 7 days ([#421](https://github.com/bokelley/salesagent/issues/421)) ([2ffbde0](https://github.com/bokelley/salesagent/commit/2ffbde077a5a4471156a79eecd8a638e3b998a85))
* Adopt SDK beta.6 compatibility helpers ([#655](https://github.com/bokelley/salesagent/issues/655)) ([a26e0e1](https://github.com/bokelley/salesagent/commit/a26e0e11a5c1460ec7500c746726153e35df7af1))
* advertise embedded compose capability state ([#623](https://github.com/bokelley/salesagent/issues/623)) ([bb33140](https://github.com/bokelley/salesagent/commit/bb331400bd930ae32d2f35297efc4d8e5b6c4246))
* Align pending creatives status semantics ([d3f150e](https://github.com/bokelley/salesagent/commit/d3f150e73805ca6e30d6d299fbf04cf0c40c843e))
* Align tenant status with wholesale products ([#696](https://github.com/bokelley/salesagent/issues/696)) ([11fee26](https://github.com/bokelley/salesagent/commit/11fee26ace6c4b1f0cfb3035eead6c36ece50042))
* allow storefront-owned approvals in embedded protocol flow ([872b66c](https://github.com/bokelley/salesagent/commit/872b66c2f92096e2a415567cff264b64f943bdcf))
* Allow tenant subdomain MCP hosts ([4d4dad8](https://github.com/bokelley/salesagent/commit/4d4dad84875561985c4cff50441a5c397773304f))
* allow wholesale get_products without search criteria ([#554](https://github.com/bokelley/salesagent/issues/554)) ([e80dec2](https://github.com/bokelley/salesagent/commit/e80dec219590df608179ac38d842654418446950))
* apply selection_type inference to inventory profile publisher_properties ([#1174](https://github.com/bokelley/salesagent/issues/1174)) ([91a97e6](https://github.com/bokelley/salesagent/commit/91a97e68dd19ab20262a5e721237f832ba5bc314))
* apply SSRF protection to signals agent URL ingestion (F-04) ([24c06b6](https://github.com/bokelley/salesagent/commit/24c06b6540cce06b748562f92c55376084bcc5a6))
* apply SSRF protection to signals agent URL ingestion (F-04) ([b5f8391](https://github.com/bokelley/salesagent/commit/b5f8391316115c1b10e1ff124f65207c5d1bfb2b))
* apply SSRF protection to signals agent URL ingestion (F-04) ([5c2d385](https://github.com/bokelley/salesagent/commit/5c2d385c9b3279e301133377cbac6780d140d683))
* **audit:** stop double-encoding audit_logs.details + repair migration ([#409](https://github.com/bokelley/salesagent/issues/409)) ([05e1370](https://github.com/bokelley/salesagent/commit/05e1370e893329596d15b38ac1384ad335c7b5e1))
* **auth:** accept Authorization: Bearer for A2A buyers ([#53](https://github.com/bokelley/salesagent/issues/53)) ([#60](https://github.com/bokelley/salesagent/issues/60)) ([da887b4](https://github.com/bokelley/salesagent/commit/da887b4cdeed05c5ca6a6af45e384a8caa124300))
* avoid empty creative format authoring catalogs ([#702](https://github.com/bokelley/salesagent/issues/702)) ([db5a0ad](https://github.com/bokelley/salesagent/commit/db5a0add323bb643c328f79201ac80ac9aa25de0))
* Bound tenant signal input sizes ([0d40b61](https://github.com/bokelley/salesagent/commit/0d40b61ed913ce860ec57c39db081e49c1eb8911))
* Bridge embedded buyer auth through SDK gate ([#683](https://github.com/bokelley/salesagent/issues/683)) ([6dacc96](https://github.com/bokelley/salesagent/commit/6dacc96d3751bf7094222c41e1b1860e02b67137))
* **broadstreet:** flip supports_inventory_sync=False until implementation lands ([#455](https://github.com/bokelley/salesagent/issues/455)) ([fd37b9d](https://github.com/bokelley/salesagent/commit/fd37b9d4b6ee9c38b9d09db586d90d4271740b1d))
* Bump AdCP SDK to 6.3.0-beta.6 ([#651](https://github.com/bokelley/salesagent/issues/651)) ([0fe3acd](https://github.com/bokelley/salesagent/commit/0fe3acd5752edce72d703d63a6375ad70e473469))
* bust browser cache on favicon re-upload ([#1255](https://github.com/bokelley/salesagent/issues/1255)) ([5cfd5eb](https://github.com/bokelley/salesagent/commit/5cfd5eba999f166a2051e4ab63575cbac399ebea)), closes [#1254](https://github.com/bokelley/salesagent/issues/1254)
* canonicalize creative format identities ([#713](https://github.com/bokelley/salesagent/issues/713)) ([7e3cc49](https://github.com/bokelley/salesagent/commit/7e3cc49786760d56bc57156f43471034aa67004a))
* canonicalize wholesale catalog creative-format refs for validation ([#719](https://github.com/bokelley/salesagent/issues/719)) ([6510b85](https://github.com/bokelley/salesagent/commit/6510b850c8a971a959c42b3ab44c704df62f783f))
* Canonicalize wholesale creative format refs ([#686](https://github.com/bokelley/salesagent/issues/686)) ([86ed4ad](https://github.com/bokelley/salesagent/commit/86ed4ad2827e01820539799e68f9d1b3d4089c88))
* chunk truncated GAM pricing reports ([a250dc6](https://github.com/bokelley/salesagent/commit/a250dc642a3955fa814866fab172150865a55036))
* chunk truncated GAM pricing reports ([a250dc6](https://github.com/bokelley/salesagent/commit/a250dc642a3955fa814866fab172150865a55036))
* chunk truncated GAM pricing reports ([d091fc2](https://github.com/bokelley/salesagent/commit/d091fc2aef4c590dbc18eb369683b60cc5b6f6b2))
* ci e2e port allocation and setting | crypto package update ([#1188](https://github.com/bokelley/salesagent/issues/1188)) ([028d68b](https://github.com/bokelley/salesagent/commit/028d68b6a5665144a46788c2af9b2a2dd916cc91))
* **ci:** reset idempotency pool between integration tests to unhang creative suite ([#134](https://github.com/bokelley/salesagent/issues/134)) ([c23550d](https://github.com/bokelley/salesagent/commit/c23550dd48f4b99092dd6def755944f8647c08b0))
* **ci:** restore green on 12 of 13 main-branch test failures ([#198](https://github.com/bokelley/salesagent/issues/198)) ([39ebd9b](https://github.com/bokelley/salesagent/commit/39ebd9b20cc5dea7aea0e6f9475f59d07eacade2))
* **ci:** unblock unit + integration suites after [#209](https://github.com/bokelley/salesagent/issues/209) webhook landing ([#212](https://github.com/bokelley/salesagent/issues/212)) ([abec7f9](https://github.com/bokelley/salesagent/commit/abec7f9cf2a39702a730cfe1f990efc582c25b18))
* clarify wholesale forecast and pricing metadata contract ([#684](https://github.com/bokelley/salesagent/issues/684)) ([d4bec1c](https://github.com/bokelley/salesagent/commit/d4bec1c9de574e9bc5e48e2333cfae1f6d4beab9))
* clear stale sync retry health after success ([#675](https://github.com/bokelley/salesagent/issues/675)) ([334e77e](https://github.com/bokelley/salesagent/commit/334e77e9d9e00b2b7bbf83d888c0f136a8d630c5))
* clear supercronic CVE scan gate ([#700](https://github.com/bokelley/salesagent/issues/700)) ([634ecd9](https://github.com/bokelley/salesagent/commit/634ecd91d6dff9ad47602e44cf64a3bb016d3d7b))
* coerce AnyUrl to str before passing to yarl.URL() ([#1106](https://github.com/bokelley/salesagent/issues/1106)) ([#1118](https://github.com/bokelley/salesagent/issues/1118)) ([4c93ee7](https://github.com/bokelley/salesagent/commit/4c93ee729577598ebd2686bf9a34fc2307724649))
* Collect orphaned core tests ([dffee76](https://github.com/bokelley/salesagent/commit/dffee76686adbb77b2fbe45d6f3cf6930aa199ad))
* **compliance:** residual fixes from 7.1.0 probe — INVALID_REQUEST, INVALID_STATE, WWW-Authenticate ([#383](https://github.com/bokelley/salesagent/issues/383)) ([71503e9](https://github.com/bokelley/salesagent/commit/71503e9698f2acfb5dfc29a22e5f59540dde657a))
* correct GAM service account authorization instructions ([#1218](https://github.com/bokelley/salesagent/issues/1218)) ([9cac65a](https://github.com/bokelley/salesagent/commit/9cac65ae244c8ddc22e44c59009684b1acef33e5))
* cover idempotency conflict wire envelopes ([#598](https://github.com/bokelley/salesagent/issues/598)) ([689f941](https://github.com/bokelley/salesagent/commit/689f94126da218ea284dab242ffe50ef62e5a8e5))
* Cover idempotency wire regressions ([#599](https://github.com/bokelley/salesagent/issues/599)) ([71782b7](https://github.com/bokelley/salesagent/commit/71782b7b87e6157875752056fce225431cdb4bd1))
* creative agent TextContent fallback for adcp SDK 3.6.0 ([#1135](https://github.com/bokelley/salesagent/issues/1135)) ([4d19a2b](https://github.com/bokelley/salesagent/commit/4d19a2be40f918919b9bd12d48126681554a79f0))
* **creative-agents:** serve stale cache + STALE_RESPONSE warning when live fetch fails ([#527](https://github.com/bokelley/salesagent/issues/527)) ([ee8809b](https://github.com/bokelley/salesagent/commit/ee8809bce32da44b15d07a7b80f85e2aa5c8e27a))
* **creative:** list_creatives honors filters.creative_ids (closes [#318](https://github.com/bokelley/salesagent/issues/318)) ([#323](https://github.com/bokelley/salesagent/issues/323)) ([670c997](https://github.com/bokelley/salesagent/commit/670c9975c45424d66a557234f2392ed6256ef418))
* **creatives:** list_creatives handles dict filters from MCP delegate ([#214](https://github.com/bokelley/salesagent/issues/214)) ([a54986e](https://github.com/bokelley/salesagent/commit/a54986eeddf999751206adeb4b1fcf65752614ed))
* **creatives:** per-creative errors must be Error objects, not strings ([#188](https://github.com/bokelley/salesagent/issues/188)) ([5603d0b](https://github.com/bokelley/salesagent/commit/5603d0b15a9a54c84520921e16eadaf481fe59a1))
* defer SpringServe demand_partner_id check to buyer-facing operations ([#688](https://github.com/bokelley/salesagent/issues/688)) ([50d0024](https://github.com/bokelley/salesagent/commit/50d0024b8f3023c094edad3b05b1b691d723290f))
* **delivery-webhook:** take a single time snapshot per scheduler batch ([#108](https://github.com/bokelley/salesagent/issues/108)) ([7757c1a](https://github.com/bokelley/salesagent/commit/7757c1accd538e544d930e630ec5de2943c1c61c))
* **delivery:** honor AdCP date-only semantics and clamp freshness target ([#211](https://github.com/bokelley/salesagent/issues/211)) ([3823a1e](https://github.com/bokelley/salesagent/commit/3823a1eba4d847d2679959ff2ac63addf20c8963)), closes [#149](https://github.com/bokelley/salesagent/issues/149) [#170](https://github.com/bokelley/salesagent/issues/170)
* **delivery:** MockSellerPlatform must return reporting_period ([#54](https://github.com/bokelley/salesagent/issues/54)) ([#84](https://github.com/bokelley/salesagent/issues/84)) ([b67fdfa](https://github.com/bokelley/salesagent/commit/b67fdfa6f98a113d525f4ab7955098fe16b64b44))
* **delivery:** populate video_completions from GAM in-stream VAST reports ([#239](https://github.com/bokelley/salesagent/issues/239)) ([7e0bc6e](https://github.com/bokelley/salesagent/commit/7e0bc6e79b29296078f3fe62b1660b4bfdbe7fc7))
* **dev:** require CONDUCTOR_PORT, recreate proxy on compose-up ([#505](https://github.com/bokelley/salesagent/issues/505)) ([0d0e99c](https://github.com/bokelley/salesagent/commit/0d0e99c46dd4465c494ae8716ca638c1ff079f8b))
* **e2e:** build_update_media_buy_request injects account + idempotency_key ([#87](https://github.com/bokelley/salesagent/issues/87)) ([#90](https://github.com/bokelley/salesagent/issues/90)) ([75c4591](https://github.com/bokelley/salesagent/commit/75c4591501a078d40539b65fa16e343ab73c1d84))
* echo create media buy idempotency keys ([224f584](https://github.com/bokelley/salesagent/commit/224f58436021bc78f531bc071b479e3cb72ada7e))
* **embedded-mode:** allow platform background workers to write adapter_config ([e3bbe7d](https://github.com/bokelley/salesagent/commit/e3bbe7d2f89d1ccfea22086b474d17e47320f3f9))
* **embedded-mode:** allow platform background workers to write adapter_config ([fcc1233](https://github.com/bokelley/salesagent/commit/fcc123333749b2cc9bd0f74dc274faa905d674e6))
* **embedded-mode:** flag targeting-manager session as platform background worker ([071a13c](https://github.com/bokelley/salesagent/commit/071a13c074d75bd2ee302d23117abe86a440c632))
* **embedded:** /status setup_tasks honors EMBEDDED_CAPABILITIES ([#483](https://github.com/bokelley/salesagent/issues/483)) ([d31c2fb](https://github.com/bokelley/salesagent/commit/d31c2fbb5dec477ac0ef2b8fa520b15c4e1cea69))
* **embedded:** allow publisher-managed writes via require_tenant_access opt-in ([#340](https://github.com/bokelley/salesagent/issues/340)) ([63b6131](https://github.com/bokelley/salesagent/commit/63b61313852cd9bb8955cbebdbdfede520222948))
* **embedded:** opt remaining read-only POST probes into embedded-write gate ([#372](https://github.com/bokelley/salesagent/issues/372)) ([6676495](https://github.com/bokelley/salesagent/commit/66764952b99fd54fc2270225bf3f884386f4c902))
* **embedded:** require_auth honors ?tenant_id= query arg for X-Identity-* bypass ([#494](https://github.com/bokelley/salesagent/issues/494)) ([bbdb8c5](https://github.com/bokelley/salesagent/commit/bbdb8c58de55704db38cfeb795d6241d65884613))
* enforce sandbox zero-rate accounts ([#661](https://github.com/bokelley/salesagent/issues/661)) ([9db569c](https://github.com/bokelley/salesagent/commit/9db569c904e72ad5bc7042e5c7483813f21cee44))
* enforce single-currency media buys ([#652](https://github.com/bokelley/salesagent/issues/652)) ([85a812f](https://github.com/bokelley/salesagent/commit/85a812fb0f4a22de7747a98cdf1ac7204007ba97))
* enforce update budget guardrails and preserve currency in media … ([#1140](https://github.com/bokelley/salesagent/issues/1140)) ([d47a04a](https://github.com/bokelley/salesagent/commit/d47a04a2fd19f62737409381f1019bda315c3d52))
* error-handling cleanup — data loss bugs, silent catches, structural guard ([#1078](https://github.com/bokelley/salesagent/issues/1078)) ([#1212](https://github.com/bokelley/salesagent/issues/1212)) ([c605026](https://github.com/bokelley/salesagent/commit/c6050266978da080488be4414ebd10e22e952c97))
* GAM test-connection must not report success with no accessible network ([#1219](https://github.com/bokelley/salesagent/issues/1219)) ([8d3a51b](https://github.com/bokelley/salesagent/commit/8d3a51b2914e6d8037001814c1fac388acd950eb))
* **gam:** align LineItem and Order payloads with GAM WSDL ([#207](https://github.com/bokelley/salesagent/issues/207)) ([04f0645](https://github.com/bokelley/salesagent/commit/04f0645caf4440b1c46ac513bc9eebb50b0cf84d)), closes [#146](https://github.com/bokelley/salesagent/issues/146) [#147](https://github.com/bokelley/salesagent/issues/147) [#148](https://github.com/bokelley/salesagent/issues/148)
* **gam:** inventory + custom-targeting sync respect service-account auth ([#61](https://github.com/bokelley/salesagent/issues/61)) ([53e444c](https://github.com/bokelley/salesagent/commit/53e444c96de3af52dc8f843d8dcafeeaa0104f2f))
* **gam:** retry update_order_dates LineItem leg on NO_FORECAST_YET ([#232](https://github.com/bokelley/salesagent/issues/232)) ([44fa29a](https://github.com/bokelley/salesagent/commit/44fa29a45daa6bba56a9541df16275bcff3376f3)), closes [#150](https://github.com/bokelley/salesagent/issues/150)
* gate storefront-owned embedded surfaces ([#612](https://github.com/bokelley/salesagent/issues/612)) ([5a73669](https://github.com/bokelley/salesagent/commit/5a73669e935555d0881ab56d67dee9b175a25d01))
* grant access when resyncing accounts ([581641a](https://github.com/bokelley/salesagent/commit/581641a4d15a572ff2cb4d3d9cd5d4ccec813e49))
* Handle embedded targeting value refresh ([#552](https://github.com/bokelley/salesagent/issues/552)) ([196b5e6](https://github.com/bokelley/salesagent/commit/196b5e62cc709b253a4ee93dcaaa673226d980b7))
* handle inapplicable GAM derived sync status ([6689aac](https://github.com/bokelley/salesagent/commit/6689aaca0b64e1c2b535ac3fcefbb72f5e5a816d))
* Handle inapplicable GAM derived sync status ([3f7f866](https://github.com/bokelley/salesagent/commit/3f7f8667251761a67048b8f33bad8b3e9ebd8bf1))
* Harden admin CSRF checks ([#588](https://github.com/bokelley/salesagent/issues/588)) ([fbcb0ad](https://github.com/bokelley/salesagent/commit/fbcb0ad07a9f617a85caf2acdbacf17c9843e037))
* harden authoring APIs and healthchecks ([#618](https://github.com/bokelley/salesagent/issues/618)) ([b815990](https://github.com/bokelley/salesagent/commit/b8159905624441825f326e7a38a806657b2483bc))
* harden get_products catalog serialization ([#569](https://github.com/bokelley/salesagent/issues/569)) ([0bb06a8](https://github.com/bokelley/salesagent/commit/0bb06a874ce93ee029550bd69b4b650bcc6dee12))
* harden inventory sync recovery ([aadceaf](https://github.com/bokelley/salesagent/commit/aadceaf803ebf22ca863a40c18f31040121b0c12))
* harden inventory sync recovery ([c1cd46d](https://github.com/bokelley/salesagent/commit/c1cd46d288a9630f669c5e52a5868bff3d679cb7))
* harden login redirect validation and test-auth gate (F-06, F-02) ([#1141](https://github.com/bokelley/salesagent/issues/1141)) ([9ab41bf](https://github.com/bokelley/salesagent/commit/9ab41bfe9ad12ccd520d81c3e54749106a945433))
* harden sync health status reporting ([8cc15f7](https://github.com/bokelley/salesagent/commit/8cc15f7ed30019e16ed2c6e0ce6be5d0bc8131d5))
* Harden targeting value live fetches ([80d76fb](https://github.com/bokelley/salesagent/commit/80d76fb7930ea5c745e42b8ea0ef03b343ce44cc))
* hide embedded-only setup tasks ([#677](https://github.com/bokelley/salesagent/issues/677)) ([48e9cc8](https://github.com/bokelley/salesagent/commit/48e9cc87c15ecdf5e21d14075ffa5dabfe189b77))
* implement empty BDD Given step bodies ([#1181](https://github.com/bokelley/salesagent/issues/1181)) ([#1185](https://github.com/bokelley/salesagent/issues/1185)) ([768d455](https://github.com/bokelley/salesagent/commit/768d455fff02926cbd0a019a53cb88dffc731a2b))
* improve FreeWheel login diagnostics ([a4b9553](https://github.com/bokelley/salesagent/commit/a4b9553e2fb5ae957d9824e9ec0f455468b915f8))
* improve FreeWheel login diagnostics ([#642](https://github.com/bokelley/salesagent/issues/642)) ([581488b](https://github.com/bokelley/salesagent/commit/581488b0b03c6be8ddba4743871a828af6a6e16f))
* Improve signals menu accessibility ([bdf7763](https://github.com/bokelley/salesagent/commit/bdf77635aaf50b666a676b54e22e8595a2b4f144))
* Include proposals in tenant exports ([c61d636](https://github.com/bokelley/salesagent/commit/c61d636d418060edec40f0601e51ec8e09169d45))
* **infra:** collect_reports return-0 + chip away at [#219](https://github.com/bokelley/salesagent/issues/219) mechanical items ([#228](https://github.com/bokelley/salesagent/issues/228)) ([72d09a7](https://github.com/bokelley/salesagent/commit/72d09a7645468e1bda474d62d215ebdb550d5c1c))
* **infra:** connection-pool resiliency for PgBouncer idle-eviction (closes [#252](https://github.com/bokelley/salesagent/issues/252)) ([#255](https://github.com/bokelley/salesagent/issues/255)) ([bd32672](https://github.com/bokelley/salesagent/commit/bd32672256e13ea8da4121af1b948aa4b25158a7))
* invalidate publisher authorization on agent URL changes ([#566](https://github.com/bokelley/salesagent/issues/566)) ([8ab9c32](https://github.com/bokelley/salesagent/commit/8ab9c32c32b9407c890299e47e90bf833baf18a8))
* **inventory-bundle:** drop shipped-alert 'Browse' buttons + polish sweep ([#550](https://github.com/bokelley/salesagent/issues/550)) ([6ca637a](https://github.com/bokelley/salesagent/commit/6ca637a697036cfe549b4015d5409d164d2ed085))
* isolate nested database sessions ([e20a657](https://github.com/bokelley/salesagent/commit/e20a6573fa8cab63ec22b634821ff242c22e95f4))
* Isolate nested database sessions ([4e2cbaa](https://github.com/bokelley/salesagent/commit/4e2cbaa5356b6e90472edfac155bd8df4d3d454c))
* lazy-loading inventory tree to prevent OOM on large GAM networks ([#1176](https://github.com/bokelley/salesagent/issues/1176)) ([382f62d](https://github.com/bokelley/salesagent/commit/382f62d81f84104f9310d63a68c3b98e8126ffdf))
* **media-buy:** _compute_status respects persisted blocker statuses (pending_creatives, paused, canceled) ([#250](https://github.com/bokelley/salesagent/issues/250)) ([0e9533c](https://github.com/bokelley/salesagent/commit/0e9533c8e948aad834f1c6eb411c4b365cae1ead))
* **media-buy:** emit submitted envelope (not hybrid) for async create_media_buy ([#183](https://github.com/bokelley/salesagent/issues/183)) ([e024a86](https://github.com/bokelley/salesagent/commit/e024a86821d001b9fc1e543bd102b6881c179d71))
* **media-buy:** emit variant-1 with MediaBuy.status=pending_creatives when buy is minted but creatives pending ([#196](https://github.com/bokelley/salesagent/issues/196)) ([e7f681c](https://github.com/bokelley/salesagent/commit/e7f681c7f4c7775302d4fb50a18be7291ffc47c3))
* **media-buy:** filter framework-injected fields in delegate coercion ([#273](https://github.com/bokelley/salesagent/issues/273)) ([#276](https://github.com/bokelley/salesagent/issues/276)) ([625e75e](https://github.com/bokelley/salesagent/commit/625e75e754c42f6b7121be76b50220df8641a034))
* **media-buy:** get_media_buys passes tenant_id to get_principal_object ([#237](https://github.com/bokelley/salesagent/issues/237)) ([05c884a](https://github.com/bokelley/salesagent/commit/05c884af9039db9b8845db7d6054de27a92a03da))
* **media-buy:** get_media_buys wire-shape validates with property_list/collection_list targeting ([#217](https://github.com/bokelley/salesagent/issues/217)) ([6fa588f](https://github.com/bokelley/salesagent/commit/6fa588fea7bf038108d4ddae831db3c0c103fc3f))
* **media-buy:** hoist update_media_buy package gate above manual-approval (closes [#251](https://github.com/bokelley/salesagent/issues/251)) ([#259](https://github.com/bokelley/salesagent/issues/259)) ([9a8a900](https://github.com/bokelley/salesagent/commit/9a8a900d1ec08451a0fe2f432064bd66cade0bd2))
* **media-buy:** map aggressive measurement_terms to TERMS_REJECTED (closes [#72](https://github.com/bokelley/salesagent/issues/72)) ([#133](https://github.com/bokelley/salesagent/issues/133)) ([ddcc694](https://github.com/bokelley/salesagent/commit/ddcc694435b5a2eea7aa7eda3279feaf8d19508d))
* **media-buy:** map IdempotencyConflictError to IDEMPOTENCY_CONFLICT wire code ([#184](https://github.com/bokelley/salesagent/issues/184)) ([36e41da](https://github.com/bokelley/salesagent/commit/36e41da2ce42af4ed80cc72e6b4184671a86432c))
* **media-buy:** NOT_CANCELLABLE error on re-cancel of canceled buy (closes [#317](https://github.com/bokelley/salesagent/issues/317)) ([#321](https://github.com/bokelley/salesagent/issues/321)) ([5fd40ef](https://github.com/bokelley/salesagent/commit/5fd40ef6607ffdb64d161bc6f49db5896fc1fc4b))
* **media-buy:** pending_creatives takes precedence over pending_start when creatives missing ([#216](https://github.com/bokelley/salesagent/issues/216)) ([df5c990](https://github.com/bokelley/salesagent/commit/df5c990f511b103e5c1fe3de533a0b7e06809542))
* **media-buy:** persist cancel before approval gate ([#551](https://github.com/bokelley/salesagent/issues/551)) ([7d0a0d2](https://github.com/bokelley/salesagent/commit/7d0a0d2b86750bc367abca96deab4b0e2a3e4735))
* **media-buy:** persist targeting_overlay from request not adapter response ([#246](https://github.com/bokelley/salesagent/issues/246)) ([c1e2b1b](https://github.com/bokelley/salesagent/commit/c1e2b1ba1206a15eacd573e9f2ef48e8cb017b0f))
* **media-buy:** update_media_buy persists targeting_overlay updates (closes [#316](https://github.com/bokelley/salesagent/issues/316)) ([#320](https://github.com/bokelley/salesagent/issues/320)) ([1f4aad3](https://github.com/bokelley/salesagent/commit/1f4aad3abe0d4d19ec3b773bd68e4363f6b38b4e))
* **media-buy:** update_media_buy with unknown package_id raises AdCPPackageNotFoundError ([#215](https://github.com/bokelley/salesagent/issues/215)) ([6434c30](https://github.com/bokelley/salesagent/commit/6434c300dc184ce236f96c3d5b1c114173327bf0))
* **media-buy:** wire MEDIA_BUY_NOT_FOUND / PACKAGE_NOT_FOUND on update_media_buy ([#128](https://github.com/bokelley/salesagent/issues/128)) ([ee8bb64](https://github.com/bokelley/salesagent/commit/ee8bb64df44678b8367f6229b645d44b7ee77dcf)), closes [#73](https://github.com/bokelley/salesagent/issues/73)
* **media-buy:** wire push_notification_config end-to-end + DRY mapping link ([#201](https://github.com/bokelley/salesagent/issues/201)) ([c1d7cb1](https://github.com/bokelley/salesagent/commit/c1d7cb1dcfdc9e4c02eda1106265cb3ec42882d3))
* **migration:** reconcile divergent alembic heads from PR [#381](https://github.com/bokelley/salesagent/issues/381) + [#409](https://github.com/bokelley/salesagent/issues/409) ([#412](https://github.com/bokelley/salesagent/issues/412)) ([9434410](https://github.com/bokelley/salesagent/commit/9434410f6264b469c4526f82436cf415febbe874))
* **migrations:** drop duplicate merge that races [#197](https://github.com/bokelley/salesagent/issues/197)'s mergepoint ([#203](https://github.com/bokelley/salesagent/issues/203)) ([5d9dc85](https://github.com/bokelley/salesagent/commit/5d9dc850a33215af2e40181170822361d0b67d33))
* **migrations:** merge advertiser_buyer_assignment with fix_duplication heads ([ea7392a](https://github.com/bokelley/salesagent/commit/ea7392a771fa97f322e08ede2987136570a14fd1))
* **migrations:** merge advertiser_buyer_assignment with fix_duplication heads ([5312105](https://github.com/bokelley/salesagent/commit/5312105efa9a55bd1987f7afb1425a60fef65172))
* **migrations:** merge divergent heads from [#186](https://github.com/bokelley/salesagent/issues/186) (ResolvedProduct) + [#193](https://github.com/bokelley/salesagent/issues/193) (gam-projection) ([#200](https://github.com/bokelley/salesagent/issues/200)) ([ef2f58f](https://github.com/bokelley/salesagent/commit/ef2f58ffb84c7d552f8e0d6376ab872489226ead))
* **mock:** sync_creatives must persist via shared delegate ([#107](https://github.com/bokelley/salesagent/issues/107)) ([21502ad](https://github.com/bokelley/salesagent/commit/21502ad4deefd4072e283fa7ec01aae78c447387))
* **mock:** update_media_buy strips stored context so SDK echoes update's ([#95](https://github.com/bokelley/salesagent/issues/95)) ([#100](https://github.com/bokelley/salesagent/issues/100)) ([72c3be3](https://github.com/bokelley/salesagent/commit/72c3be31d685cdc0fcda6cfbe289b5a0d4f06166))
* normalize admin UI to canonical /admin routes ([090fcdd](https://github.com/bokelley/salesagent/commit/090fcdd0a0f66e9fead42b3c89092ec18e8d2c67))
* normalize domains in property filtering to handle www/m subdomains ([#1207](https://github.com/bokelley/salesagent/issues/1207)) ([fa075b2](https://github.com/bokelley/salesagent/commit/fa075b2f431b37442ed51c54c092b3ac8b9cfa85))
* Optimize signals list performance ([a97e5bc](https://github.com/bokelley/salesagent/commit/a97e5bc2c6b7f989e50884c3000c68de4ff1c8ea))
* parallelize creative agent format fetch with global timeout cap ([#525](https://github.com/bokelley/salesagent/issues/525)) ([9baba84](https://github.com/bokelley/salesagent/commit/9baba84d65cac0af8af0a9d40e030409a29c70b9))
* persist platform_line_item_ids in execute_approved_media_buy ([#1126](https://github.com/bokelley/salesagent/issues/1126)) ([be021ec](https://github.com/bokelley/salesagent/commit/be021ec855c2de0f17a8f3215953755a447367d1))
* pin multi-tenant deployment to production mode ([#660](https://github.com/bokelley/salesagent/issues/660)) ([2541a13](https://github.com/bokelley/salesagent/commit/2541a132c68164cfc35d3de95108024503804006))
* **pre-commit:** no-tenant-config regex over-matches webhook event names ([#33](https://github.com/bokelley/salesagent/issues/33)) ([ad2b096](https://github.com/bokelley/salesagent/commit/ad2b09661faeb82584d5b8a61b16448cb8b22f96)), closes [#28](https://github.com/bokelley/salesagent/issues/28)
* Preserve wholesale pricing options ([f336d11](https://github.com/bokelley/salesagent/commit/f336d119041acf59b3dbfba0cc1bcd2c1b1d003d))
* Project wholesale products from inventory bundles ([#679](https://github.com/bokelley/salesagent/issues/679)) ([c1f0226](https://github.com/bokelley/salesagent/commit/c1f022605d0740659b213d13b9612db5a32615fd))
* **provision:** make provisioning synchronous + binary, no polling ([#441](https://github.com/bokelley/salesagent/issues/441)) ([e670644](https://github.com/bokelley/salesagent/commit/e6706441cf668a64a6b7a49e93cb4904fd0c2591))
* **quality:** make code-duplication ratchet stable to file deletions ([#140](https://github.com/bokelley/salesagent/issues/140)) ([36006de](https://github.com/bokelley/salesagent/commit/36006de3891cc9e60e375d69e3404b73ba32a0d5))
* raise MCP server startup timeout in CI ([#635](https://github.com/bokelley/salesagent/issues/635)) ([6f2e16b](https://github.com/bokelley/salesagent/commit/6f2e16b801b7d51a67768957bdf56a18720a7ebb))
* raise on anomalous empty format responses instead of silent return [] ([#1167](https://github.com/bokelley/salesagent/issues/1167)) ([f326cbe](https://github.com/bokelley/salesagent/commit/f326cbec52a807ed133710e8eeebf9eb4fbac287))
* reduce GAM startup DB and geo regressions ([#622](https://github.com/bokelley/salesagent/issues/622)) ([d1b8ec0](https://github.com/bokelley/salesagent/commit/d1b8ec0f2367268584d0fa0eef38452effddc85b))
* reject inactive tenant service tokens ([#650](https://github.com/bokelley/salesagent/issues/650)) ([418c2af](https://github.com/bokelley/salesagent/commit/418c2af61fd03369c8e4181463d899b1e08412f9))
* Reject invalid creative format agents ([#641](https://github.com/bokelley/salesagent/issues/641)) ([3fb14f9](https://github.com/bokelley/salesagent/commit/3fb14f9e80780613d1152a0355534d01821dfa4c))
* Reject tenant IDs containing colons ([#590](https://github.com/bokelley/salesagent/issues/590)) ([50d25d8](https://github.com/bokelley/salesagent/commit/50d25d869b4f573935d7cf525bd900a852058726))
* reject unknown MCP fields in dev mode ([#603](https://github.com/bokelley/salesagent/issues/603)) ([d8fc382](https://github.com/bokelley/salesagent/commit/d8fc382ffc925e95383041b5734bae904ff57c4e))
* Remove auth-chain account default ([e5b05f8](https://github.com/bokelley/salesagent/commit/e5b05f8a2b44b4be3fac678f736d0c28509b9fc5))
* remove unauthenticated /init-api-key endpoint and harden control-plane auth ([#1103](https://github.com/bokelley/salesagent/issues/1103)) ([681d5d2](https://github.com/bokelley/salesagent/commit/681d5d2062442a55a338a0d70e595f3bc9e38b98))
* Repair admin action controls ([883656e](https://github.com/bokelley/salesagent/commit/883656e84e405935bca57901abd3ead0a1f78aab))
* replace broken adcontextprotocol.org auth setup guide link ([#1252](https://github.com/bokelley/salesagent/issues/1252)) ([#1253](https://github.com/bokelley/salesagent/issues/1253)) ([050d3e5](https://github.com/bokelley/salesagent/commit/050d3e591d8591cd679e793fa31136336511f4a8))
* replace dict subscript with attribute access on FormatId objects ([#1166](https://github.com/bokelley/salesagent/issues/1166)) ([cbfa7bd](https://github.com/bokelley/salesagent/commit/cbfa7bd5d1eb55dad43296a676ac967fffae60d9))
* require authenticated principal for task management tools ([#1139](https://github.com/bokelley/salesagent/issues/1139)) ([b52dcdc](https://github.com/bokelley/salesagent/commit/b52dcdcae3e830ee60b07a1780a8231ebe5369b0))
* Require HTTPS webhook delivery URLs ([#589](https://github.com/bokelley/salesagent/issues/589)) ([08ee816](https://github.com/bokelley/salesagent/commit/08ee8163dd0c2dd921f7e5a0ffefdbf6965396a5))
* resolve CI failures on PR [#1143](https://github.com/bokelley/salesagent/issues/1143) ([98c8cb8](https://github.com/bokelley/salesagent/commit/98c8cb8c5f01670a119e67a8fc6ce066f034c92c))
* resolve forked Alembic migration graph and prevent recurrence ([#1144](https://github.com/bokelley/salesagent/issues/1144)) ([46369f9](https://github.com/bokelley/salesagent/commit/46369f9533130169d7a83abb37d2a88bbfd000f8))
* Resolve publisher_properties dict selectors ([#575](https://github.com/bokelley/salesagent/issues/575)) ([65435fb](https://github.com/bokelley/salesagent/commit/65435fb673a489c40a38871f862007578b2633bf))
* Resolve stale custom targeting sync health ([#673](https://github.com/bokelley/salesagent/issues/673)) ([2eedf46](https://github.com/bokelley/salesagent/commit/2eedf46c1d0d438142a7cbf8cd6b890d92fbcf35))
* restore pre-[#1066](https://github.com/bokelley/salesagent/issues/1066) admin routes via flask fallback mount ([4204334](https://github.com/bokelley/salesagent/commit/4204334bcd129e89090acf22b4df6044de4c6ac4))
* Return absolute tenant management surface URLs ([3e92e63](https://github.com/bokelley/salesagent/commit/3e92e63c5ce62323c6ab9c0596cb44ffe30244c7))
* rewrite cross-tenant test for modern security model ([#52](https://github.com/bokelley/salesagent/issues/52)) ([#58](https://github.com/bokelley/salesagent/issues/58)) ([b5aeddf](https://github.com/bokelley/salesagent/commit/b5aeddfed82f835b5c42581379ee766f360658f4))
* **schema, gam:** defuse canceled default-injection + repair package_id parser ([#238](https://github.com/bokelley/salesagent/issues/238)) ([b280001](https://github.com/bokelley/salesagent/commit/b2800016654ac2ba0b29972effcce9087814a833)), closes [#155](https://github.com/bokelley/salesagent/issues/155) [#153](https://github.com/bokelley/salesagent/issues/153)
* **schema:** make reporting_capabilities non-null at the DB layer (closes [#71](https://github.com/bokelley/salesagent/issues/71)) ([#110](https://github.com/bokelley/salesagent/issues/110)) ([c21e578](https://github.com/bokelley/salesagent/commit/c21e578082ebd5f15cb832112ec2ce67c46f2a8d))
* Scope publisher setup to wholesale products ([#608](https://github.com/bokelley/salesagent/issues/608)) ([d6173ac](https://github.com/bokelley/salesagent/commit/d6173acd975bc01d4362cbd8051b61402ff5ef37))
* **scripts:** harden run_all_tests.sh against pipefail aborts ([#432](https://github.com/bokelley/salesagent/issues/432)) ([#454](https://github.com/bokelley/salesagent/issues/454)) ([b6a4cc7](https://github.com/bokelley/salesagent/commit/b6a4cc7765ddf2d8082469ca8be84759c542d0b1))
* secure agent credentials and GAM import revocation ([#573](https://github.com/bokelley/salesagent/issues/573)) ([4a7ecc4](https://github.com/bokelley/salesagent/commit/4a7ecc4641dd0d3c5ec490edbc1dc81d09e8f6d6))
* **security:** build supercronic from source on Go 1.26.3 + bump temporalio to clear Trivy CVEs ([1dce653](https://github.com/bokelley/salesagent/commit/1dce653da28aaf857369ae3981c339a58ad68302))
* **security:** build supercronic from source on Go 1.26.3 + bump temporalio to clear Trivy CVEs ([ff08a64](https://github.com/bokelley/salesagent/commit/ff08a6424802a4d59aada12f58bc7d98edfe1791))
* **security:** bump supercronic to v0.2.45 to clear Go stdlib CVEs ([4ef41b6](https://github.com/bokelley/salesagent/commit/4ef41b618d0cc647e12657573527670c26883303))
* **security:** bump supercronic v0.2.41 → v0.2.45 to clear Go stdlib CVEs ([6341679](https://github.com/bokelley/salesagent/commit/6341679517139673d3e0523dac30f142d26f056e))
* **security:** require auth on publisher_partners routes (closes [#65](https://github.com/bokelley/salesagent/issues/65)) ([#79](https://github.com/bokelley/salesagent/issues/79)) ([48257fd](https://github.com/bokelley/salesagent/commit/48257fd9082ca07c880803569055b693ce4bd8b7))
* **security:** SSRF gate on publisher_domain (closes [#80](https://github.com/bokelley/salesagent/issues/80)) ([#98](https://github.com/bokelley/salesagent/issues/98)) ([66bec43](https://github.com/bokelley/salesagent/commit/66bec433aad99ef9c46cc6f4a084c8787c7d3b14))
* **seed:** bootstrap tenant_management_api_key in seed_demo_data ([#440](https://github.com/bokelley/salesagent/issues/440)) ([dd1b034](https://github.com/bokelley/salesagent/commit/dd1b03439a7b04c21bc22a06304d7952727ff0ae))
* Self-heal local example publisher authorization ([fa08a99](https://github.com/bokelley/salesagent/commit/fa08a994425e50b96faedfc2fbd07a30361da658))
* set default GAM advertiser from cache on tenant provision ([#687](https://github.com/bokelley/salesagent/issues/687)) ([0678f8a](https://github.com/bokelley/salesagent/commit/0678f8ad6cba0fff051e5583b51799acd5400164))
* set management_api_caller flag during inventory sync to bypass embedded-tenant guard ([78a5f26](https://github.com/bokelley/salesagent/commit/78a5f266bd535b7f4524367aa3e5a9e158454bc0))
* set management_api_caller flag during inventory sync to bypass embedded-tenant guard ([300129f](https://github.com/bokelley/salesagent/commit/300129f5bac5c1f70601a246728d1d79e1f9db80))
* **setup:** AAO checklist accepts any resolvable agent URL, not just explicit column ([#174](https://github.com/bokelley/salesagent/issues/174)) ([22176c2](https://github.com/bokelley/salesagent/commit/22176c20f3a9a86ff2bc407ec80947fa3930cbaf))
* **signals:** unescaped JSON literal in form re-render JS ([#464](https://github.com/bokelley/salesagent/issues/464)) ([71fa628](https://github.com/bokelley/salesagent/commit/71fa628bf36cb6728a84241f46bcf495717afde1))
* **signing:** post-slice-3 housekeeping ([#195](https://github.com/bokelley/salesagent/issues/195)) ([3a2b094](https://github.com/bokelley/salesagent/commit/3a2b09401ef90e8b50ff21c3b382f94ecc61e0e1))
* Skip format validation when creative agent returns no formats ([#1137](https://github.com/bokelley/salesagent/issues/1137)) ([2e237dc](https://github.com/bokelley/salesagent/commit/2e237dc93dac06f4296634df55c453d9b54830e0))
* **storyboard:** close 4 storyboard failures, wire sync_accounts/list_accounts ([#313](https://github.com/bokelley/salesagent/issues/313)) ([cb8557d](https://github.com/bokelley/salesagent/commit/cb8557d26da3be729e5c926ca75da99b998d9986))
* support catalog webhooks and publisher property selectors ([#636](https://github.com/bokelley/salesagent/issues/636)) ([46954f9](https://github.com/bokelley/salesagent/commit/46954f9bc3848512c718c7ef35c9f3286e5191e5))
* support GAM ad unit pricing availability ([#698](https://github.com/bokelley/salesagent/issues/698)) ([329a758](https://github.com/bokelley/salesagent/commit/329a75829a787c203364b725d3fa92f90570e721))
* suppress GAM reporting sync as inapplicable in tenant status ([#717](https://github.com/bokelley/salesagent/issues/717)) ([d1a4ed7](https://github.com/bokelley/salesagent/commit/d1a4ed73225cef6c1614f7a1cb351ee78d22672d))
* surface empty GAM advertiser sync failures ([#723](https://github.com/bokelley/salesagent/issues/723)) ([3b53d7f](https://github.com/bokelley/salesagent/commit/3b53d7f6de5e19cb1f8b35eed5d384c0e192ac01))
* surface incomplete wholesale products ([#721](https://github.com/bokelley/salesagent/issues/721)) ([e7f0531](https://github.com/bokelley/salesagent/commit/e7f05314c8cd67f54fa4740d66bb4b7f76f8f6d0))
* **tenant-export:** remap globally-unique string PKs on retarget (closes [#416](https://github.com/bokelley/salesagent/issues/416)) ([#417](https://github.com/bokelley/salesagent/issues/417)) ([b20d186](https://github.com/bokelley/salesagent/commit/b20d186a91b354838921d608624cf9f24f6dbaeb))
* **tenant-export:** strip + remap autoincrement int PKs when retargeting ([#415](https://github.com/bokelley/salesagent/issues/415)) ([fcda6d2](https://github.com/bokelley/salesagent/commit/fcda6d2da5eb816e02a6ddf36579b38e8b200037))
* **tenant-export:** suspend user triggers during bulk tenant delete ([#414](https://github.com/bokelley/salesagent/issues/414)) ([0d77b70](https://github.com/bokelley/salesagent/commit/0d77b70017a91da802b8fdbe5221632d659877aa))
* **test:** align reporting_period end with date-only end-of-day semantics ([#211](https://github.com/bokelley/salesagent/issues/211) follow-up) ([9bc9fbe](https://github.com/bokelley/salesagent/commit/9bc9fbe86a41745cf6955467a23ad7ee32682c39))
* **test:** include pending_creatives in targeting-overlay roundtrip filter ([#257](https://github.com/bokelley/salesagent/issues/257)) ([813f02a](https://github.com/bokelley/salesagent/commit/813f02ae9bde9a499f6c366b079ab48158a557ea))
* **test:** make test_sync_accounts_premap skip cleanly when DATABASE_URL absent ([#139](https://github.com/bokelley/salesagent/issues/139)) ([8849780](https://github.com/bokelley/salesagent/commit/884978040135aafdd1de696020675b6d60cb9143))
* **tests:** sweep test-debt — admin/MCP/error-code fixes ([#438](https://github.com/bokelley/salesagent/issues/438)) ([78bb135](https://github.com/bokelley/salesagent/commit/78bb1351b65c2fa4d1c6eecc2b730a4c72eb9160))
* Tolerate ADCP beta envelope and catalog webhooks ([#632](https://github.com/bokelley/salesagent/issues/632)) ([cb692a3](https://github.com/bokelley/salesagent/commit/cb692a3c4bed8bb96713ace8ab2e35ce9870689e))
* tolerate single-placement GAM pricing caps ([5a07c90](https://github.com/bokelley/salesagent/commit/5a07c90c79ea05e9698d132bb24f1ae3236c47a8))
* tolerate single-placement GAM pricing caps ([5a07c90](https://github.com/bokelley/salesagent/commit/5a07c90c79ea05e9698d132bb24f1ae3236c47a8))
* tolerate single-placement GAM pricing caps ([5b4dada](https://github.com/bokelley/salesagent/commit/5b4dadad7daf314a7fe5959ca25d4825f155ac49))
* **transport:** env-gated stateless MCP mode for multi-replica deploys ([#376](https://github.com/bokelley/salesagent/issues/376)) ([8cdb985](https://github.com/bokelley/salesagent/commit/8cdb985e1eb6b4c3468928fa4a164e29ccf5e7e0))
* **transport:** translate pydantic.ValidationError to INVALID_REQUEST; cross-transport wire contract test ([#330](https://github.com/bokelley/salesagent/issues/330)) ([e71a943](https://github.com/bokelley/salesagent/commit/e71a94369fb85a9c0fed5547a288ab1e167d2054))
* **update_media_buy:** impl-layer idempotency replay (defence in depth) ([#236](https://github.com/bokelley/salesagent/issues/236)) ([0016587](https://github.com/bokelley/salesagent/commit/0016587c4f63b26550917c97f30baf1e556312b8)), closes [#168](https://github.com/bokelley/salesagent/issues/168)
* **update_media_buy:** pre-flight refuse reservation changes on guaranteed line items ([#233](https://github.com/bokelley/salesagent/issues/233)) ([973d600](https://github.com/bokelley/salesagent/commit/973d600bd3a17a3ea1ecaaf655b3b4fa6b9e9543)), closes [#156](https://github.com/bokelley/salesagent/issues/156)
* **update_media_buy:** surface workflow_step_id when deferred for approval ([#235](https://github.com/bokelley/salesagent/issues/235)) ([0aa26c2](https://github.com/bokelley/salesagent/commit/0aa26c23fe524e033c8993b1d507f4566f7da6b1)), closes [#158](https://github.com/bokelley/salesagent/issues/158)
* **update_media_buy:** sync flight-date changes to GAM Order + LineItems ([#231](https://github.com/bokelley/salesagent/issues/231)) ([965dd4b](https://github.com/bokelley/salesagent/commit/965dd4b1bbb42b586b79c9e44386008803e846d7)), closes [#157](https://github.com/bokelley/salesagent/issues/157)
* **update:** all 8 UpdateMediaBuy responses echo request.context ([#91](https://github.com/bokelley/salesagent/issues/91)) ([#92](https://github.com/bokelley/salesagent/issues/92)) ([8553830](https://github.com/bokelley/salesagent/commit/855383071d8c7a58854beb7fcbc6915b88f1ff72))
* use request.script_root for dynamic URL prefixing ([#1160](https://github.com/bokelley/salesagent/issues/1160)) ([53ebf1b](https://github.com/bokelley/salesagent/commit/53ebf1be75ff4a6302c34379f8a96e386434aaaa))
* use SDK-native webhook signing capabilities ([#617](https://github.com/bokelley/salesagent/issues/617)) ([71364fc](https://github.com/bokelley/salesagent/commit/71364fc3ec719a3304a4cdfd36b945b2965f4a23))
* validate product formats with typed FormatId ([#712](https://github.com/bokelley/salesagent/issues/712)) ([4575dcf](https://github.com/bokelley/salesagent/commit/4575dcf33e1c13b6261b5acff352f2d3d5569ca6))
* Validate standard creative format aliases locally ([#639](https://github.com/bokelley/salesagent/issues/639)) ([d6ef058](https://github.com/bokelley/salesagent/commit/d6ef058d8d0a673ec05d54a643f379ff37234263))
* video/display format template appears unselected after saving product ([#1168](https://github.com/bokelley/salesagent/issues/1168)) ([95a4168](https://github.com/bokelley/salesagent/commit/95a4168f08d8320557827235a285a78a29026f85))
* **webhooks:** thread inbound transport so A2A buyers receive Task envelopes ([#209](https://github.com/bokelley/salesagent/issues/209)) ([f1c3f03](https://github.com/bokelley/salesagent/commit/f1c3f03e8ebb96cf05220817f8329dd20281cf07))
* **webhooks:** warn-once when _build_identity falls back to protocol='mcp' inside an authenticated request ([#227](https://github.com/bokelley/salesagent/issues/227)) ([09e0131](https://github.com/bokelley/salesagent/commit/09e0131d08bb0a2ebfa7f9b8932db55492d1bc00))
* **workflows:** replay update_media_buy on approval, not just create ([#229](https://github.com/bokelley/salesagent/issues/229)) ([5c1af94](https://github.com/bokelley/salesagent/commit/5c1af9458b85d78668e585a1842166e481370cb8)), closes [#143](https://github.com/bokelley/salesagent/issues/143)
* wrap account dict in AccountReference and normalise list-form assignments in sync_creatives ([#1251](https://github.com/bokelley/salesagent/issues/1251)) ([460122a](https://github.com/bokelley/salesagent/commit/460122a8acfd8d02921c5c2719a2f3a68522a077))


### Performance Improvements

* **ci:** parallelize integration tests + local mock creative agent ([#1148](https://github.com/bokelley/salesagent/issues/1148)) ([e4995d7](https://github.com/bokelley/salesagent/commit/e4995d714f68a1fc5a762e2ce74e99fd5f1219a8))
* Slim Python runtime image ([30066d0](https://github.com/bokelley/salesagent/commit/30066d028853c263de6466870a0d7f10f613693f))


### Code Refactoring

* AdapterConfigRepository + GAM service account auth consolidation ([#1171](https://github.com/bokelley/salesagent/issues/1171)) ([49714f8](https://github.com/bokelley/salesagent/commit/49714f846aa3f5b0229c7efa3c0d57e1255b450b))
* **adapters:** GAM uses _validate_targeting_or_error base helper ([#161](https://github.com/bokelley/salesagent/issues/161)) ([414b841](https://github.com/bokelley/salesagent/commit/414b8419c14387b2c7bf44a799e59dcc0ee3c77b))
* **admin-ui:** finish breadcrumb removal + subnav polish (closes [#282](https://github.com/bokelley/salesagent/issues/282)) ([#286](https://github.com/bokelley/salesagent/issues/286)) ([977d8c4](https://github.com/bokelley/salesagent/commit/977d8c4ae09fad784280e59784e410940b417402))
* **admin-ui:** replace breadcrumbs with persistent tenant subnav ([#284](https://github.com/bokelley/salesagent/issues/284)) ([169b3cb](https://github.com/bokelley/salesagent/commit/169b3cb373a10027715226785f13c2ddc3edae42))
* **admin:** drop redundant X-Identity-Subject CSRF bypass ([#424](https://github.com/bokelley/salesagent/issues/424)) ([99e6b94](https://github.com/bokelley/salesagent/commit/99e6b94dbaee3f5b928303470b81877a823cdcb0))
* **auth:** drop BearerToAdcpAuthMiddleware shim, use adcp 4.5.0 per-leg config ([#194](https://github.com/bokelley/salesagent/issues/194)) ([d8f5a7a](https://github.com/bokelley/salesagent/commit/d8f5a7a0ad3f3e65f2f8ebee431588be762894c2))
* complete delivery schema extraction from _base.py ([#1121](https://github.com/bokelley/salesagent/issues/1121)) ([534da98](https://github.com/bokelley/salesagent/commit/534da986b01e2a5294752bbe8739f1f858036e8f))
* **conversion:** simplify convert_product_model_to_schema (Phase 1 slice 3) ([#167](https://github.com/bokelley/salesagent/issues/167)) ([9e4ef72](https://github.com/bokelley/salesagent/commit/9e4ef72ce95dbee147542f6a6793f5b6c789002f))
* **dashboard:** reframe inventory coverage as bundle-reference (no review/skip) ([#509](https://github.com/bokelley/salesagent/issues/509)) ([df0c451](https://github.com/bokelley/salesagent/commit/df0c4514af4b2fec375f7eb38399fde5d7543bd7))
* delete legacy FastAPI/A2A/REST stack and flat-param tool wrappers ([#17](https://github.com/bokelley/salesagent/issues/17)) ([9105402](https://github.com/bokelley/salesagent/commit/910540298b0a3acd4bdce0d2e0042e811591ad2f))
* **deploy:** kill bundled nginx, route admin host + prod subdomains ([#25](https://github.com/bokelley/salesagent/issues/25)) ([98ea239](https://github.com/bokelley/salesagent/commit/98ea239b121a13177f9dea15182b87d365b664a5))
* **docker:** strip nginx binary + configs out of runtime image ([#34](https://github.com/bokelley/salesagent/issues/34)) ([25b19f3](https://github.com/bokelley/salesagent/commit/25b19f389ef386c3f01f4d9ae61a84e02dc5f198))
* drop SchedulerLifespanMiddleware, use serve(on_startup=, on_shutdown=) ([#401](https://github.com/bokelley/salesagent/issues/401)) ([ab3ee75](https://github.com/bokelley/salesagent/commit/ab3ee7504a9c63aceb9a17a91541a41953c126eb))
* Eliminate get_db_session() from business logic — complete repository pattern adoption ([#1097](https://github.com/bokelley/salesagent/issues/1097)) ([cd73f34](https://github.com/bokelley/salesagent/commit/cd73f34ac5861e25a63cdb678c79eeb61897fed5))
* **embedded-mode:** tighten review feedback on platform_background_worker ([f43ead7](https://github.com/bokelley/salesagent/commit/f43ead7049e71adfc5ddebf6cf9ca0c97ee30b60))
* Enable Ruff B904 ([33a7229](https://github.com/bokelley/salesagent/commit/33a7229d2847b34d59795166d065faabc1f85633))
* extract shared delivery helpers and migrate all adapters ([#1124](https://github.com/bokelley/salesagent/issues/1124)) ([238698c](https://github.com/bokelley/salesagent/commit/238698c74d5bdf66f6405568f3872e8090dac283))
* extract shared helpers in property discovery service ([#1206](https://github.com/bokelley/salesagent/issues/1206)) ([d97b4f3](https://github.com/bokelley/salesagent/commit/d97b4f344e8b2d16b77e724c99f7d45c9522d30e))
* **idempotency:** drop dead AdCPIdempotencyConflictError + ship [#178](https://github.com/bokelley/salesagent/issues/178) coverage in CI ([#294](https://github.com/bokelley/salesagent/issues/294)) ([94ac13d](https://github.com/bokelley/salesagent/commit/94ac13dda9b8403de972552419ecf46ae2789bba))
* **media-buy-update:** collapse 22 .model_dump() sites into serialize_for_workflow_step ([#240](https://github.com/bokelley/salesagent/issues/240)) ([#241](https://github.com/bokelley/salesagent/issues/241)) ([43f7f92](https://github.com/bokelley/salesagent/commit/43f7f92c483980703882b2b081ac1477c8852801))
* **media-buy:** stop mutating Product schema in GAM config gate (Phase 2 slice 1) ([#175](https://github.com/bokelley/salesagent/issues/175)) ([3d3096f](https://github.com/bokelley/salesagent/commit/3d3096f963788996ef0f18569239c60c1413c6a7))
* **mock:** drop _MEDIA_BUYS, delegate to DB-backed _impl across the board ([#118](https://github.com/bokelley/salesagent/issues/118)) ([4dcd4cb](https://github.com/bokelley/salesagent/commit/4dcd4cb3e09f84735e2673967f6f2f6789d4aa7c))
* move billing policy and approval mode to tenant configuration ([#1184](https://github.com/bokelley/salesagent/issues/1184)) ([#1186](https://github.com/bokelley/salesagent/issues/1186)) ([77fcc4c](https://github.com/bokelley/salesagent/commit/77fcc4ca0230d6b21a0ca0bc9a35547860054491))
* post-[#515](https://github.com/bokelley/salesagent/issues/515) cleanup (dead MCPAuthMiddleware, list_authorized_properties, list_accounts gate) ([#518](https://github.com/bokelley/salesagent/issues/518)) ([aed406a](https://github.com/bokelley/salesagent/commit/aed406a64f008ec87f28dc34e573db435a5154f8))
* **products:** collapse Product subclass into LibraryProduct (Phase 2 slice 6) ([#230](https://github.com/bokelley/salesagent/issues/230)) ([f5a35ff](https://github.com/bokelley/salesagent/commit/f5a35ff6d72819a6054d4f17f63c9977f345946a))
* **products:** drop GetProductsRequest extension, enforce spec (Phase 2 slice 7) ([#245](https://github.com/bokelley/salesagent/issues/245)) ([fe2b571](https://github.com/bokelley/salesagent/commit/fe2b57170c05d5f6e06373add58403c01d1ac40d))
* **products:** drop internal fields from Product schema (Phase 2 slice 5) ([#226](https://github.com/bokelley/salesagent/issues/226)) ([bca4a6d](https://github.com/bokelley/salesagent/commit/bca4a6df395b82fbef940bd9313246b2a66744a0))
* **products:** migrate get_product_catalog to ResolvedProduct (Phase 2 slice 4) ([#206](https://github.com/bokelley/salesagent/issues/206)) ([920d1f2](https://github.com/bokelley/salesagent/commit/920d1f2851dc4259da5f489c760e9a7fb832129f))
* **products:** migrate get_products filter pipeline to ResolvedProduct (Phase 2 slice 3) ([#197](https://github.com/bokelley/salesagent/issues/197)) ([082052e](https://github.com/bokelley/salesagent/commit/082052e24f0d6505b8af3cb4696facf2844dc1bc))
* Remove adapter-specific OpenAPI setup specs ([#613](https://github.com/bokelley/salesagent/issues/613)) ([d9b6362](https://github.com/bokelley/salesagent/commit/d9b6362c02d4a8277b18a3297ef8121e014b95c8))
* Replace star import re-exports ([c6599a6](https://github.com/bokelley/salesagent/commit/c6599a615c597e85f0c49df9ef4870185e0c34f2))
* **schemas:** drop dead Creative.variants wire-extension ([#253](https://github.com/bokelley/salesagent/issues/253)) ([437ff03](https://github.com/bokelley/salesagent/commit/437ff03dec512c048c184a4183a70fed78a79cf5))
* **schemas:** drop dead FrequencyCap.scope wire-extension ([#244](https://github.com/bokelley/salesagent/issues/244)) ([32f5a41](https://github.com/bokelley/salesagent/commit/32f5a419bef8553c7374b8e2b6828586f04a7c4a))
* **schemas:** drop dead Product validators (Phase 1 slice 4) ([#171](https://github.com/bokelley/salesagent/issues/171)) ([04dbd4e](https://github.com/bokelley/salesagent/commit/04dbd4e4644787846165995c9e1703b77b32549e))
* **schemas:** drop dead Targeting dimensions ([#280](https://github.com/bokelley/salesagent/issues/280) Wave A) ([#285](https://github.com/bokelley/salesagent/issues/285)) ([6efa059](https://github.com/bokelley/salesagent/commit/6efa0592ac21b52e89110ace32950faf9ed77e11))
* **schemas:** drop dead v2-compat layer (Phase 1 slice 5) ([#172](https://github.com/bokelley/salesagent/issues/172)) ([d0d6201](https://github.com/bokelley/salesagent/commit/d0d6201f309fc34e6b6145374d5708f419dc4b2f))
* **schemas:** drop MediaPackage.cpm — package_pricing_info is sole pricing source (closes [#266](https://github.com/bokelley/salesagent/issues/266)) ([#287](https://github.com/bokelley/salesagent/issues/287)) ([d03c355](https://github.com/bokelley/salesagent/commit/d03c355756af8dbeaba45d7e9f72079dfe6ccd49))
* **schemas:** drop Product.model_dump override (Phase 1 slice 2) ([#124](https://github.com/bokelley/salesagent/issues/124)) ([91ee0f4](https://github.com/bokelley/salesagent/commit/91ee0f46a3f01c3486705f9fcdf6d72874f21434))
* **schemas:** drop UpdateMediaBuyRequest.budget — migrate to ext.salesagent.budget ([#260](https://github.com/bokelley/salesagent/issues/260)) ([1ef73c3](https://github.com/bokelley/salesagent/commit/1ef73c3aefb8d735a798e36739eb540fb238663c))
* **schemas:** GetMediaBuysRequest/Response → Pattern [#1](https://github.com/bokelley/salesagent/issues/1) inheritance (closes [#262](https://github.com/bokelley/salesagent/issues/262)) ([#283](https://github.com/bokelley/salesagent/issues/283)) ([a92f877](https://github.com/bokelley/salesagent/commit/a92f8774540dd3c7bfdb3d91b1a0edfeedc589ce))
* **schemas:** per-field exclude on PricingOption internal fields ([#281](https://github.com/bokelley/salesagent/issues/281)) ([1a8b45d](https://github.com/bokelley/salesagent/commit/1a8b45d10d2ff9e282509ad64325670b4c082e2d))
* **schemas:** rename DeliveryTotals.video_completions → completed_views ([#278](https://github.com/bokelley/salesagent/issues/278)) ([34dd2ec](https://github.com/bokelley/salesagent/commit/34dd2ecc5e7b37d63f9c7cadd5557a7f62258189))
* **schemas:** rename Targeting → TargetingOverlay + per-field exclude=True ([#264](https://github.com/bokelley/salesagent/issues/264) Phase 1) ([#279](https://github.com/bokelley/salesagent/issues/279)) ([bc0c05a](https://github.com/bokelley/salesagent/commit/bc0c05ab24b2edc5f69d7aac4a662d524b28c514))
* **schemas:** split sync wire into CreativeAsset (closes [#265](https://github.com/bokelley/salesagent/issues/265), partial) ([#288](https://github.com/bokelley/salesagent/issues/288)) ([20bcb5a](https://github.com/bokelley/salesagent/commit/20bcb5a0541d91817a9792982a5fc2ae9fbe7115))
* **schemas:** systematic cleanup of stale AdCP-spec annotations ([#208](https://github.com/bokelley/salesagent/issues/208)) ([58daa92](https://github.com/bokelley/salesagent/commit/58daa922eec76256563e6b77273e27aa58ebbe76))
* swap AgentCardPublicUrlMiddleware for public_url callable ([#402](https://github.com/bokelley/salesagent/issues/402)) ([c2f7967](https://github.com/bokelley/salesagent/commit/c2f7967b71f15f78d2c8d768304e9bf2af127bf4))
* **templates:** tokenize 23 form templates and components against design system ([#21](https://github.com/bokelley/salesagent/issues/21)) ([a8c6e00](https://github.com/bokelley/salesagent/commit/a8c6e00b0ac3e159c9985fcdd96b0fd0e963f589))
* **types:** adopt SchemaVariant for 12 cross-class schema overrides ([#400](https://github.com/bokelley/salesagent/issues/400)) ([3d6e32d](https://github.com/bokelley/salesagent/commit/3d6e32d251ae8b8a108f9e9096c5937e3849f221))
* use adcp.decisioning state graph for media_buy terminal-state guard ([#389](https://github.com/bokelley/salesagent/issues/389)) ([3ddb2c8](https://github.com/bokelley/salesagent/commit/3ddb2c84ac9cd0b1c946ce7c2578ca692102ecd1))


### Documentation

* add architecture patterns reference for contributors ([#1145](https://github.com/bokelley/salesagent/issues/1145)) ([aaa1bf2](https://github.com/bokelley/salesagent/commit/aaa1bf2cbb4483c886f28d8fb1cd5d697ce5630d))
* **embedded:** correct OpenAPI spec URL and remove shipped sprint design docs ([#493](https://github.com/bokelley/salesagent/issues/493)) ([f8432f7](https://github.com/bokelley/salesagent/commit/f8432f7055cc37cc116cbffb5b636397d820dd5e))
* **embedded:** update sprint-7 + sprint-4 + parent embedded-mode after IA cleanup landing ([#474](https://github.com/bokelley/salesagent/issues/474)) ([6cd9c3f](https://github.com/bokelley/salesagent/commit/6cd9c3fc55571008770f5fa0ed1bad43338a1790))
* **idempotency:** record decision not to rebuild pool on DATABASE_URL change ([#181](https://github.com/bokelley/salesagent/issues/181)) ([ccda8c2](https://github.com/bokelley/salesagent/commit/ccda8c24a77a61170478403216f02301620ca636))
* lessons from storyboard compliance work ([#334](https://github.com/bokelley/salesagent/issues/334)) ([4e26fbd](https://github.com/bokelley/salesagent/commit/4e26fbde68ef9634f3f66655862fba798d7ec2d7))
* publish Tenant Management OpenAPI spec at repo root ([#450](https://github.com/bokelley/salesagent/issues/450)) ([09d253d](https://github.com/bokelley/salesagent/commit/09d253d1d54e9c3e3ddd6fd764901553d8b21204))
* Update idempotency escape hatch comment ([894a23c](https://github.com/bokelley/salesagent/commit/894a23c3c90819a66b38c2de165e9a0c91b116bc))

## [1.7.0](https://github.com/prebid/salesagent/compare/v1.6.0...v1.7.0) (2026-04-09)


### Features

* Account management, adcp 3.10 migration, and BDD test infrastructure ([#1170](https://github.com/prebid/salesagent/issues/1170)) ([ccf91eb](https://github.com/prebid/salesagent/commit/ccf91eb933466598a7532cd24542e18df4236f0f))
* introduce BDD behavioral test suite (delivery metrics, creative formats) ([#1146](https://github.com/prebid/salesagent/issues/1146)) ([7f0d45a](https://github.com/prebid/salesagent/commit/7f0d45a4e7eb5beeb02657c66e69d5365e3e5a31))
* universal request normalization for AdCP backward compatibility ([#1175](https://github.com/prebid/salesagent/issues/1175)) ([1ad11b6](https://github.com/prebid/salesagent/commit/1ad11b67a8c42cd0aaada1f3924fee1cd0a4be3d))


### Bug Fixes

* add missing AdCP spec fields to UpdateMediaBuyRequest and correct e2e assertions ([#1152](https://github.com/prebid/salesagent/issues/1152)) ([e9a7a67](https://github.com/prebid/salesagent/commit/e9a7a674ba7b224f7c3f1ff24098cf320cdd586f))
* apply SSRF protection to signals agent URL ingestion (F-04) ([c18ac11](https://github.com/prebid/salesagent/commit/c18ac111b09f8078a69d3df7ea0805a189d1c94f))
* ci e2e port allocation and setting | crypto package update ([#1188](https://github.com/prebid/salesagent/issues/1188)) ([e48edda](https://github.com/prebid/salesagent/commit/e48edda516e39a07433ed464d05c4177af8ec3c8))
* enforce update budget guardrails and preserve currency in media … ([#1140](https://github.com/prebid/salesagent/issues/1140)) ([1e1aa6d](https://github.com/prebid/salesagent/commit/1e1aa6d03bcdfdc07baabc4c5c0656d0d3d8cfd7))
* harden login redirect validation and test-auth gate (F-06, F-02) ([#1141](https://github.com/prebid/salesagent/issues/1141)) ([cd56496](https://github.com/prebid/salesagent/commit/cd56496bb594c96a9c5082fc3544fe3d0850780b))
* raise on anomalous empty format responses instead of silent return [] ([#1167](https://github.com/prebid/salesagent/issues/1167)) ([149f58b](https://github.com/prebid/salesagent/commit/149f58b819d1f99bbaa61a9d50c9dcf8353547eb))
* replace dict subscript with attribute access on FormatId objects ([#1166](https://github.com/prebid/salesagent/issues/1166)) ([80c5776](https://github.com/prebid/salesagent/commit/80c577631164757a49f871a7be145bbbf23c2759))
* require authenticated principal for task management tools ([#1139](https://github.com/prebid/salesagent/issues/1139)) ([ff1006d](https://github.com/prebid/salesagent/commit/ff1006ddc9f967b7bf745a390465198b40ed29a2))
* resolve CI failures on PR [#1143](https://github.com/prebid/salesagent/issues/1143) ([5c5f34f](https://github.com/prebid/salesagent/commit/5c5f34f0f93e473817ac7e936e00dc851b985ff1))
* use request.script_root for dynamic URL prefixing ([#1160](https://github.com/prebid/salesagent/issues/1160)) ([0b304ed](https://github.com/prebid/salesagent/commit/0b304ed589c7e7a01cabb21a84e4479ad1869db9))
* video/display format template appears unselected after saving product ([#1168](https://github.com/prebid/salesagent/issues/1168)) ([9e395c5](https://github.com/prebid/salesagent/commit/9e395c5dc2b8eb800483a2dc18bc533fe040452e))


### Performance Improvements

* **ci:** parallelize integration tests + local mock creative agent ([#1148](https://github.com/prebid/salesagent/issues/1148)) ([9c65617](https://github.com/prebid/salesagent/commit/9c6561776b78d19a0670bc8a0edf93a518ae142b))


### Code Refactoring

* AdapterConfigRepository + GAM service account auth consolidation ([#1171](https://github.com/prebid/salesagent/issues/1171)) ([5e89166](https://github.com/prebid/salesagent/commit/5e891667d0eb8a332dd70131fd16429d55925179))


### Documentation

* add architecture patterns reference for contributors ([#1145](https://github.com/prebid/salesagent/issues/1145)) ([2d61ea1](https://github.com/prebid/salesagent/commit/2d61ea1133b24eb66f0e8ae8bb319827cf7c2c4e))

## [1.6.0](https://github.com/prebid/salesagent/compare/v1.5.0...v1.6.0) (2026-03-19)


### Features

* consolidate security-sensitive code — SSRF protection and OAuth normalization ([#1125](https://github.com/prebid/salesagent/issues/1125)) ([a683f86](https://github.com/prebid/salesagent/commit/a683f8693aecb37c69285606ffa177eb0043875b))


### Bug Fixes

* coerce AnyUrl to str before passing to yarl.URL() ([#1106](https://github.com/prebid/salesagent/issues/1106)) ([#1118](https://github.com/prebid/salesagent/issues/1118)) ([641ee9e](https://github.com/prebid/salesagent/commit/641ee9e10b529bf1cfcdf6e571fe1d68b48aa2fa))
* creative agent TextContent fallback for adcp SDK 3.6.0 ([#1135](https://github.com/prebid/salesagent/issues/1135)) ([d83ed14](https://github.com/prebid/salesagent/commit/d83ed14dfac25828d5d46eb872a930d25bacc194))
* normalize admin UI to canonical /admin routes ([0283124](https://github.com/prebid/salesagent/commit/02831240a2f286b33ca86e5def8771cfdbb617e4))
* persist platform_line_item_ids in execute_approved_media_buy ([#1126](https://github.com/prebid/salesagent/issues/1126)) ([6a9776d](https://github.com/prebid/salesagent/commit/6a9776d1f8cd647f1b6b054dd6314746b1da95a0))
* remove unauthenticated /init-api-key endpoint and harden control-plane auth ([#1103](https://github.com/prebid/salesagent/issues/1103)) ([3a336ef](https://github.com/prebid/salesagent/commit/3a336efb412465f5c345cc87bf020b4118381f31))
* resolve forked Alembic migration graph and prevent recurrence ([#1144](https://github.com/prebid/salesagent/issues/1144)) ([a4cd866](https://github.com/prebid/salesagent/commit/a4cd8666f3e33bc5ad8ab4cb906af722c2c99917))
* restore pre-[#1066](https://github.com/prebid/salesagent/issues/1066) admin routes via flask fallback mount ([4b919f3](https://github.com/prebid/salesagent/commit/4b919f3b95e4cf2757f8f5e92ddf822fcb506e5e))
* Skip format validation when creative agent returns no formats ([#1137](https://github.com/prebid/salesagent/issues/1137)) ([1173473](https://github.com/prebid/salesagent/commit/1173473c6dcdcf17f9a52530d984165752dff5da))


### Code Refactoring

* complete delivery schema extraction from _base.py ([#1121](https://github.com/prebid/salesagent/issues/1121)) ([46624e9](https://github.com/prebid/salesagent/commit/46624e99508b377bc3b9f33b3181c8b3f272c5d3))
* Eliminate get_db_session() from business logic — complete repository pattern adoption ([#1097](https://github.com/prebid/salesagent/issues/1097)) ([1965f1d](https://github.com/prebid/salesagent/commit/1965f1df3b0ce4719845db6ee2c40d17b6358ddc))
* extract shared delivery helpers and migrate all adapters ([#1124](https://github.com/prebid/salesagent/issues/1124)) ([e8a9b8a](https://github.com/prebid/salesagent/commit/e8a9b8a320a267d4b44899ce4b40a77db69f625e))

## [1.5.0](https://github.com/prebid/salesagent/compare/v1.4.0...v1.5.0) (2026-03-09)


### Features

* AdCP v3.6 upgrade — schema migration, auth hardening, repository pattern, multi-tenant isolation ([#1071](https://github.com/prebid/salesagent/issues/1071)) ([3398aab](https://github.com/prebid/salesagent/commit/3398aabf387447ccfbd20402703a60ff99f62bd5))
* Creative domain completion — v3.6 schema, auth hardening, error propagation, 3300+ tests ([#1080](https://github.com/prebid/salesagent/issues/1080)) ([0cbe97c](https://github.com/prebid/salesagent/commit/0cbe97cf33d240cb10d4388091c126fb541baf22))
* delivery domain completion + media buy test coverage (v3.6) ([#1081](https://github.com/prebid/salesagent/issues/1081)) ([46db70f](https://github.com/prebid/salesagent/commit/46db70fcc38cdec6d84d663fa7bfc547f4e86100))
* Error recovery classification and standard error vocabulary ([#1083](https://github.com/prebid/salesagent/issues/1083)) ([96ed70f](https://github.com/prebid/salesagent/commit/96ed70fa72e3b594693dd1f94dc0acb548750f9b))
* Product v3.6 completion — schema extraction, repository pattern, obligation test coverage ([#1082](https://github.com/prebid/salesagent/issues/1082)) ([90e1dfa](https://github.com/prebid/salesagent/commit/90e1dfad0c5809544be6375a29571077be8174ae))


### Bug Fixes

* resolve FormatId AttributeError crashing Add/Edit Product pages ([#1079](https://github.com/prebid/salesagent/issues/1079)) ([0b22f1e](https://github.com/prebid/salesagent/commit/0b22f1e4f54a219bd3e5cd54879c52872e9c468e))


### Code Refactoring

* FastAPI migration — unify MCP + A2A + Admin into single process ([#1066](https://github.com/prebid/salesagent/issues/1066)) ([7d2b1d9](https://github.com/prebid/salesagent/commit/7d2b1d9e05d30388c74259ec29bd03f24390e2e7))

## [1.4.0](https://github.com/prebid/salesagent/compare/v1.3.1...v1.4.0) (2026-02-27)


### Features

* Add Broadstreet Ads adapter with template support ([#1013](https://github.com/prebid/salesagent/issues/1013)) ([d7db92e](https://github.com/prebid/salesagent/commit/d7db92e75fd9b62f49647daf8f71728063a817a9))
* implement get_media_buys tool with delivery snapshots ([#1063](https://github.com/prebid/salesagent/issues/1063)) ([0ebcf93](https://github.com/prebid/salesagent/commit/0ebcf935abe48ed782a859daacb74d9f5e4404a7))
* Support AdCP v3 structured geo targeting ([#1006](https://github.com/prebid/salesagent/issues/1006)) ([#1024](https://github.com/prebid/salesagent/issues/1024)) ([ec3939a](https://github.com/prebid/salesagent/commit/ec3939af437687903fdb5272202b66961f7e5389))


### Bug Fixes

* Add root-level URL fallback for simple creatives ([843ab76](https://github.com/prebid/salesagent/commit/843ab761fb7254f4c3497907805eb3ad6670ccce))
* bump googleads to 49.0.0 and remove GAM_API_VERSION constant ([#1070](https://github.com/prebid/salesagent/issues/1070)) ([f6ce2a9](https://github.com/prebid/salesagent/commit/f6ce2a94bb7750d413d6d293fe163a14480129cf))
* handle FormatId objects in format validation during media buy creation ([3e9dcaf](https://github.com/prebid/salesagent/commit/3e9dcaf48ceff522453e00f28367250c3237629a))
* improve test harness stability and add real GAM e2e tests ([#1062](https://github.com/prebid/salesagent/issues/1062)) ([52dc231](https://github.com/prebid/salesagent/commit/52dc2310410879ad1d8a5962951a7ebd872245d9))
* propagate delivery_type in GAM products_map for correct line item type selection ([#1058](https://github.com/prebid/salesagent/issues/1058)) ([ff36add](https://github.com/prebid/salesagent/commit/ff36add62ffa59a32db62b7248eb1356cf4b4ce4))
* resolve property_ids/property_tags authorization in property discovery ([#1054](https://github.com/prebid/salesagent/issues/1054)) ([a188b6a](https://github.com/prebid/salesagent/commit/a188b6aaff9f211e186bf6c1b0fd29a2eb14fd5a))
* Unify creative URL extraction and update GAM macro mappings ([56cbc6a](https://github.com/prebid/salesagent/commit/56cbc6a2b335f90fe93f32ba5e830901e335621d))
* Unify creative URL extraction and update GAM macro mappings ([70b04e2](https://github.com/prebid/salesagent/commit/70b04e2e18a266ff94cb0ca9cf46377b6c1f326f))
* Update vulnerable dependencies (cryptography, pillow) ([4ba6c61](https://github.com/prebid/salesagent/commit/4ba6c612a582a81edb421902cce787ede53a41f5))
* use attribute access for FormatId in format validation during media buy creation ([502f978](https://github.com/prebid/salesagent/commit/502f97838c3c8f17b6a84ed742c6cbe7dabea02f)), closes [#1019](https://github.com/prebid/salesagent/issues/1019)


### Code Refactoring

* eliminate model_dump antipatterns and migrate to adcp library base classes ([#1051](https://github.com/prebid/salesagent/issues/1051)) ([5e6815f](https://github.com/prebid/salesagent/commit/5e6815f53f5ab0fb115b3cd2e88c8a69ab770991))
* enforce typed model boundaries across serialization and data flow ([#1044](https://github.com/prebid/salesagent/issues/1044)) ([c412ce9](https://github.com/prebid/salesagent/commit/c412ce9e0cd46852511b153f99f43aade759678a))

## [1.3.1](https://github.com/prebid/salesagent/compare/v1.3.0...v1.3.1) (2026-02-06)


### Bug Fixes

* Convert FormatId dicts to objects for GAM creative placeholders ([#1016](https://github.com/prebid/salesagent/issues/1016)) ([0a6f2a2](https://github.com/prebid/salesagent/commit/0a6f2a20d7a7ebad01cf4e3f19a58aaaa43d7f89))

## [1.3.0](https://github.com/prebid/salesagent/compare/v1.2.0...v1.3.0) (2026-02-04)


### Features

* Add schema-driven adapter configuration ([#1007](https://github.com/prebid/salesagent/issues/1007)) ([e6324b6](https://github.com/prebid/salesagent/commit/e6324b6d4ebd1d7ce97ad012312d94a441ffc4d9))
* Display version in tenant landing page footer ([27780c5](https://github.com/prebid/salesagent/commit/27780c517cf63bb1d9404ae59f29287a7a67e39f))
* Display version in tenant landing page footer ([37015e6](https://github.com/prebid/salesagent/commit/37015e62be09e31bac0d8505f9310c2bfd48ab2a))


### Bug Fixes

* Add audit logging for get_products, update_media_buy, and update_performance_index ([9dfd39b](https://github.com/prebid/salesagent/commit/9dfd39b26e7838f89fd7f24569a12b73aa79a530))
* Add audit logging for get_products, update_media_buy, and update_performance_index ([d563dc8](https://github.com/prebid/salesagent/commit/d563dc8dc354cc412475410d583a8dcaddf6151a))
* Correct comment to match actual implementation (starts with, not contains) ([666f889](https://github.com/prebid/salesagent/commit/666f889a543cdf9f7623af6f0d041b5dff9d1644))
* Improve GAM creative-to-line-item matching for flexible naming templates ([126f0dd](https://github.com/prebid/salesagent/commit/126f0dd4ba2f61df47295f9eb09c61f24fc9efe1))
* Improve GAM creative-to-line-item matching for flexible naming templates ([7d85208](https://github.com/prebid/salesagent/commit/7d85208fb50378b8d654fb7256158ac11a9083cb))
* more tests ([88b0347](https://github.com/prebid/salesagent/commit/88b0347a155784b501ce03552ff1b736690f4e80))
* mypy ([0aeb111](https://github.com/prebid/salesagent/commit/0aeb111ceb25e3ba120bfbf0b40c4c6c3c461f4d))
* preserve format dimensions during media buy approval ([1bdc4a7](https://github.com/prebid/salesagent/commit/1bdc4a7fa0f6bd55748f4d57e1f9974edd5f6276))
* preserve format dimensions during media buy approval ([dc2429e](https://github.com/prebid/salesagent/commit/dc2429ed3b6d3e29e30b30fae6b9ac8b38d22444))
* Preserve signup flow state through OAuth redirect ([7e63052](https://github.com/prebid/salesagent/commit/7e63052fffad9b5a9b9e9d865073bb347f5febd6))
* Preserve signup flow state through OAuth redirect ([735211d](https://github.com/prebid/salesagent/commit/735211d2783bba2aff995e327f0cfea20ddce50b))
* reset tests to main ([03e32db](https://github.com/prebid/salesagent/commit/03e32db5eb97cb2c9791ba3fd555e686ad56608d))
* tests ([491e21e](https://github.com/prebid/salesagent/commit/491e21ea02e9f733773d48bf3bbb7ecb21212f3f))
* Update organization and repository names in ipr sig workflow ([3217152](https://github.com/prebid/salesagent/commit/32171525fd495394c28c2ba3bbb70122c68b55d6))
* Use dynamic dates in GAM pricing restriction tests ([bef48d1](https://github.com/prebid/salesagent/commit/bef48d1fdd4674529656f6ae2a577f89e9d739f9))
* Use dynamic dates in pricing integration tests ([a89dc8a](https://github.com/prebid/salesagent/commit/a89dc8ad7477e2ceb6a65750206f925060e571d1))


### Documentation

* Add PR naming guideline to CLAUDE.md ([34a87be](https://github.com/prebid/salesagent/commit/34a87be7d7104b54ff3e5570bffb4cf918156758))

## [1.2.0](https://github.com/prebid/salesagent/compare/v1.1.0...v1.2.0) (2026-01-29)


### Features

* Add get_adcp_capabilities tool for AdCP v3 compliance ([#973](https://github.com/prebid/salesagent/issues/973)) ([407a495](https://github.com/prebid/salesagent/commit/407a49550ea2a68d33429db05aafa10bc31c2369))


### Bug Fixes

* ipr policy should point to prebid.org ([1fad905](https://github.com/prebid/salesagent/commit/1fad905b2888b8e2cc023c541b3bd1fdc464d18f))
* Remove unnecessary trafficker_id requirement from GAM creatives_manager ([#975](https://github.com/prebid/salesagent/issues/975)) ([87fad8a](https://github.com/prebid/salesagent/commit/87fad8a9208fd1a64ff3e50822a99420e34552b7))


### Documentation

* fix repo loc ([03ffce8](https://github.com/prebid/salesagent/commit/03ffce8d5dcae6e43542ed9dc62890cb9787f4c6))

## [1.1.0](https://github.com/prebid/salesagent/compare/v1.0.0...v1.1.0) (2026-01-26)


### Features

* Add dry_run mode support for create/update media buy operations ([#970](https://github.com/prebid/salesagent/issues/970)) ([e9aac61](https://github.com/prebid/salesagent/commit/e9aac61b0cf08c1861d2b287853737cd68475dd2))


### Bug Fixes

* Add v2.x backward compatibility for pricing_options and clean up production logs ([#971](https://github.com/prebid/salesagent/issues/971)) ([0992131](https://github.com/prebid/salesagent/commit/0992131a5ccbd7b93a66409a262d20aeeb83ce28))

## [1.0.0](https://github.com/prebid/salesagent/compare/v0.9.3...v1.0.0) (2026-01-26)


### ⚠ BREAKING CHANGES

* This updates the AdCP library dependency from 2.14.0 to 3.0.0.

### Features

* Migrate to AdCP 3.0.0 library ([#968](https://github.com/prebid/salesagent/issues/968)) ([e4b31b2](https://github.com/prebid/salesagent/commit/e4b31b2ee2747db86c6d8dc352e58f0491d498ee))

## [0.9.3](https://github.com/prebid/salesagent/compare/v0.9.2...v0.9.3) (2026-01-25)


### Bug Fixes

* Handle product.format_ids as dicts in creative validation ([#965](https://github.com/prebid/salesagent/issues/965)) ([1c760e6](https://github.com/prebid/salesagent/commit/1c760e68c43a975505555214c7357f570c0df572))
* ignore url not configured when use mock adapters ([#943](https://github.com/prebid/salesagent/issues/943)) ([7f55e02](https://github.com/prebid/salesagent/commit/7f55e021d9bc53273c55b9394a4173a81fd62e69))

## [0.9.2](https://github.com/prebid/salesagent/compare/v0.9.1...v0.9.2) (2026-01-22)


### Bug Fixes

* Construct FormatId from DB creative's agent_url and format columns ([#961](https://github.com/prebid/salesagent/issues/961)) ([b22dbff](https://github.com/prebid/salesagent/commit/b22dbffe619a835231624fd5c3d751406c8cc5ff))

## [0.9.1](https://github.com/prebid/salesagent/compare/v0.9.0...v0.9.1) (2026-01-19)


### Bug Fixes

* Sync custom targeting keys to adapter_config during inventory sync ([fa187f9](https://github.com/prebid/salesagent/commit/fa187f9c4689b256df0b12bd25b4552832de99a9))
* Sync custom targeting keys to adapter_config during inventory sync ([d49badc](https://github.com/prebid/salesagent/commit/d49badc385de4377698ad58a76fa2751ee1621ed))

## [0.9.0](https://github.com/prebid/salesagent/compare/v0.8.0...v0.9.0) (2026-01-16)


### Features

* Add role name fallback for tracker detection and fix REDIRECT_URL macro ([4054133](https://github.com/prebid/salesagent/commit/405413384895081ad7f8d10d02680e04f14aa322))
* Add tracker_redirect support with REDIRECTION_URL macro ([6b34831](https://github.com/prebid/salesagent/commit/6b348318df29e0c856886a56097c2daa75c85d06))
* Add tracking pixel macro substitution for GAM adapter ([a3dd2d1](https://github.com/prebid/salesagent/commit/a3dd2d1d77146bb8d7b5edacf683c5f82c729e46))


### Bug Fixes

* Add isinstance check for list before append in creative_helpers ([8b42656](https://github.com/prebid/salesagent/commit/8b4265681c879059f790304b5ced1e105f121278))
* Improve click tracker and native creative tracking handling ([ade8de4](https://github.com/prebid/salesagent/commit/ade8de43cbcebab42d4109206e28e24b8506c054))
* Pass tenant_gemini_key as keyword argument to build_order_name_context ([5f4f1f1](https://github.com/prebid/salesagent/commit/5f4f1f1d9ba353b989370482b49674ccd04c173c))
* Restore click tracking URL support via destinationUrl ([c3eeab7](https://github.com/prebid/salesagent/commit/c3eeab7bf0c76f6470bdf0d497d530fdc78e29db))

## [0.8.0](https://github.com/prebid/salesagent/compare/v0.7.0...v0.8.0) (2026-01-14)


### Features

* update to adcp 2.18.0 with new assets field support ([6d19499](https://github.com/prebid/salesagent/commit/6d1949929b24f35299bd6ef60f73626fc9e10e55))
* update to adcp 2.18.0 with new assets field support ([4c94434](https://github.com/prebid/salesagent/commit/4c944340338ca0d12b1942e7bebf82d559670ec2))


### Bug Fixes

* Accept Authorization: Bearer header for MCP authentication ([#948](https://github.com/prebid/salesagent/issues/948)) ([a1ae3ff](https://github.com/prebid/salesagent/commit/a1ae3ffd6704a0dbf670964f021f305bb44be8b0))
* Fix CI test failures and security vulnerabilities ([f1e4c40](https://github.com/prebid/salesagent/commit/f1e4c40a172dbaddf74b951bf12052ddd7a0daff))
* Improve onboarding experience and resolve first-run issues ([#946](https://github.com/prebid/salesagent/issues/946)) ([e803e85](https://github.com/prebid/salesagent/commit/e803e8521d7cc3b6a40a0a359bfee0fb26e278e5))
* resolve mypy type errors for adcp 2.18.0 format assets ([7d0a021](https://github.com/prebid/salesagent/commit/7d0a0214cfe14aad667be0663e066fae3a1fb731))
* Update adcp to 2.18.0 for assets field support ([9693fcc](https://github.com/prebid/salesagent/commit/9693fccf5a56cde40e07045315b5cdf238bde0ae))
* Update adcp to 2.18.0 for assets field support ([ebfb27d](https://github.com/prebid/salesagent/commit/ebfb27dfdfc2586def28c43313bc5084cb89ede3))
* update tests for adcp 2.18.0 compatibility ([0a60085](https://github.com/prebid/salesagent/commit/0a60085e7c3bf402e008ed7c1c707da7c043bd66))
* update urllib3 and werkzeug to fix security vulnerabilities ([9490db8](https://github.com/prebid/salesagent/commit/9490db82b9cb369b31d7ed0af0ea9d8a2b6cf6df))
* use dynamic adcp version in e2e tests instead of hardcoded 2.5.0 ([ecee6f2](https://github.com/prebid/salesagent/commit/ecee6f297c47be85a348620e2f642e22ab619525))


### Code Refactoring

* rename asset_req to asset_spec for clarity ([f15c9ea](https://github.com/prebid/salesagent/commit/f15c9ea944ff9c80969909620ddf8ae82657207a))


### Documentation

* clarify that repeatable groups were never supported in asset extraction ([dbee49e](https://github.com/prebid/salesagent/commit/dbee49ef5d847d90127fe90f026dc5fe9e60f9ca))

## [0.7.0](https://github.com/prebid/salesagent/compare/v0.6.0...v0.7.0) (2026-01-08)


### Features

* Add tenant-configurable favicon support ([#940](https://github.com/prebid/salesagent/issues/940)) ([f8b1696](https://github.com/prebid/salesagent/commit/f8b1696f2939314d6c3973eed1a7b66108b5ebc1))


### Bug Fixes

* a2a bugs with media buy and media buy delivery ([c5325b9](https://github.com/prebid/salesagent/commit/c5325b9982f4d3afa1559542cac8e4a023834fba))
* a2a bugs with media buy and media buy delivery ([ea98357](https://github.com/prebid/salesagent/commit/ea98357d30e6ded327cc0eda8ed8ea4d2c91aaa5))
* Add security audit to CI and upgrade fastmcp ([#941](https://github.com/prebid/salesagent/issues/941)) ([ec592ed](https://github.com/prebid/salesagent/commit/ec592edf3789b7e3b92a7060ca89e29d1721dfab))
* Include empty pricing_options in serialization for anonymous users ([#939](https://github.com/prebid/salesagent/issues/939)) ([4e57265](https://github.com/prebid/salesagent/commit/4e57265631d73258959dcb8021601d4346599ae9))
* Set default role to admin for SSO auto-provisioned users ([#937](https://github.com/prebid/salesagent/issues/937)) ([e64440c](https://github.com/prebid/salesagent/commit/e64440c2c5253ab9b387132403d5ab1ae66378b0))

## [0.6.0](https://github.com/prebid/salesagent/compare/v0.5.0...v0.6.0) (2026-01-05)


### Features

* Add GAM placement targeting for creative-level targeting (adcp[#208](https://github.com/prebid/salesagent/issues/208)) ([#915](https://github.com/prebid/salesagent/issues/915)) ([b2f9585](https://github.com/prebid/salesagent/commit/b2f9585660eee9098c26f22adcf49636e1472ca7))
* apply suggestions ([362513f](https://github.com/prebid/salesagent/commit/362513fdfdcae1323d48b3d3ec2076142c131c66))
* apply suggestions ([9b75990](https://github.com/prebid/salesagent/commit/9b759903506f7cd9b49be5de068b65e848351c28))
* improve e2e test for a2a push notification delivery v2 ([f9008b9](https://github.com/prebid/salesagent/commit/f9008b9ccb4e96fdcbaba93bc2234e2f2f1804c5))
* Make SSO optional for multi-tenant deployments ([#931](https://github.com/prebid/salesagent/issues/931)) ([8ac80a1](https://github.com/prebid/salesagent/commit/8ac80a143957dcf29e8b51457ec4f4e4cf44237d))
* migrate push notification sending for media_buy ([6fa4cda](https://github.com/prebid/salesagent/commit/6fa4cda0a587d7d4846f22641c5b6f44dab13298))
* undo unrelated changes ([9a7e45f](https://github.com/prebid/salesagent/commit/9a7e45f38f130b9de6efbb1b362dba2555ba4e62))
* update webhook delivery function to support both mcp and a2a payloads ([7f41d98](https://github.com/prebid/salesagent/commit/7f41d989ad254280b6cfd6c138352e4404242bff))
* update webhook delivery function to support both mcp and a2a payloads ([27b2eaa](https://github.com/prebid/salesagent/commit/27b2eaad01a7ad94939de710743f39bb79d2e61d))
* wip ([0713543](https://github.com/prebid/salesagent/commit/07135438371946932469d6676bc9bbd45add0acc))


### Bug Fixes

* adcp version; media buy status change; media buy delivery look up ([41dd1dc](https://github.com/prebid/salesagent/commit/41dd1dc5a9fae67020e106589d9471a5eb8705e6))
* Add Fly.io header middleware for proper HTTPS detection ([#920](https://github.com/prebid/salesagent/issues/920)) ([a115fc9](https://github.com/prebid/salesagent/commit/a115fc9f0e0f1a4f71385843ed80e074833b7482))
* Add multi-admin domain support for cross-domain OAuth ([#919](https://github.com/prebid/salesagent/issues/919)) ([f373ebb](https://github.com/prebid/salesagent/commit/f373ebb1350fe55798b270a9ad8155c905593e5f))
* Clear session before OAuth to prevent stale cookie conflicts ([#924](https://github.com/prebid/salesagent/issues/924)) ([addab84](https://github.com/prebid/salesagent/commit/addab84e7d3b280d3d157c416fb988d468b14d87))
* Correct middleware ordering for Fly.io header processing ([#921](https://github.com/prebid/salesagent/issues/921)) ([c4d373d](https://github.com/prebid/salesagent/commit/c4d373dcc04ccae7074e426f24997f2c4d5ab212))
* e2e webhook delivery check ([1599266](https://github.com/prebid/salesagent/commit/159926689db9a986ef3bb8ef55359a864b3cd3b9))
* Explicitly save session on OAuth redirect to persist state cookie ([#928](https://github.com/prebid/salesagent/issues/928)) ([e78ae67](https://github.com/prebid/salesagent/commit/e78ae67a1cd2f7638117a87f6a37823e678d2c8f))
* Fix list_creatives enum serialization and invalid creative count ([#930](https://github.com/prebid/salesagent/issues/930)) ([3d9c643](https://github.com/prebid/salesagent/commit/3d9c64368a8956f8acc7948201be1c55c31906a5))
* improve tests ([676690b](https://github.com/prebid/salesagent/commit/676690bf82ef3aa750e553e0e9f3a75933344ec1))
* integration test v2 ([756d6d4](https://github.com/prebid/salesagent/commit/756d6d4cf7381fd63c9acdd6a2721bc33ac3fbcf))
* integrations ([8050605](https://github.com/prebid/salesagent/commit/805060535f16c5fb3bf7ce3d5168fa91af27efff))
* link validator check for cyclic bugs ([91fe6f7](https://github.com/prebid/salesagent/commit/91fe6f70e8d7ee0919bc79f72657c8bd76b3ae02))
* mypy failures ([79de36d](https://github.com/prebid/salesagent/commit/79de36d4393aac56614e19b83a32a2963f028a24))
* Preserve tenant context on OAuth callback errors ([#918](https://github.com/prebid/salesagent/issues/918)) ([c82760b](https://github.com/prebid/salesagent/commit/c82760beddae4a9c4f376ae22395b680a2412466))
* Preserve X-Forwarded-Proto from Fly.io through nginx ([#922](https://github.com/prebid/salesagent/issues/922)) ([5eddd36](https://github.com/prebid/salesagent/commit/5eddd36dd2d2e118e47db9f1f810470f2bde89ab))
* Prevent redirect loop for super admins accessing /admin/ ([#929](https://github.com/prebid/salesagent/issues/929)) ([95d7cac](https://github.com/prebid/salesagent/commit/95d7cac07d98fe06b292f90de6a65f04689e8ab8))
* Restore deleted migration to fix Fly.io deploy ([#914](https://github.com/prebid/salesagent/issues/914)) ([2cfbccc](https://github.com/prebid/salesagent/commit/2cfbccce8a0a2850f30d22f73970b3642cc28f1a))
* Reuse unwrapped brand_manifest for policy checks ([#932](https://github.com/prebid/salesagent/issues/932)) ([#935](https://github.com/prebid/salesagent/issues/935)) ([03ba408](https://github.com/prebid/salesagent/commit/03ba40841de09a85656f1d17cc864348ab8836eb))
* Route multi-tenant subdomain requests to tenant-specific login ([#916](https://github.com/prebid/salesagent/issues/916)) ([c0152db](https://github.com/prebid/salesagent/commit/c0152dbce4d1965cf7f500e366cc5013092c6e87))
* Run database migrations automatically on docker compose up ([#933](https://github.com/prebid/salesagent/issues/933)) ([c5c73a8](https://github.com/prebid/salesagent/commit/c5c73a80f356ca5c149026938ffdd7ae6b515c94))
* run_all_tests ci ([3a1b1d2](https://github.com/prebid/salesagent/commit/3a1b1d2f05780ad8990d149073d8fb38c6838aa2))
* Show full values in Pydantic extra_forbidden errors ([#912](https://github.com/prebid/salesagent/issues/912)) ([165d985](https://github.com/prebid/salesagent/commit/165d985805edd5b35bdfbfec0768150f1b7a4696))
* Task and TaskStatusUpdate serializations ([a8e7792](https://github.com/prebid/salesagent/commit/a8e77926bfa5237e7dc2bdf74ba09d092bba7488))
* Use global OAuth as fallback, not setup mode for multi-tenant ([#917](https://github.com/prebid/salesagent/issues/917)) ([ef63349](https://github.com/prebid/salesagent/commit/ef63349191e0f8002d304db6f9feda92b8b0cbc4))


### Documentation

* Update Docker Compose documentation to reflect nginx proxy architecture ([#934](https://github.com/prebid/salesagent/issues/934)) ([5503768](https://github.com/prebid/salesagent/commit/55037689c4ed5e58bebf24e65710c2ae8e646349))

## [0.5.0](https://github.com/prebid/salesagent/compare/v0.4.1...v0.5.0) (2026-01-01)


### Features

* Add dynamic per-tenant OIDC/SSO authentication ([#903](https://github.com/prebid/salesagent/issues/903)) ([ed05a41](https://github.com/prebid/salesagent/commit/ed05a4131ea4fffa212ab1e72a243650d0a493b5))
* Add format template picker UI for AdCP 2.5 parameterized formats ([#782](https://github.com/prebid/salesagent/issues/782)) ([#882](https://github.com/prebid/salesagent/issues/882)) ([532657e](https://github.com/prebid/salesagent/commit/532657ec6b796f12d40a2c41860b67bc4c0fca62))
* Add vidium MCP server to local configuration ([#904](https://github.com/prebid/salesagent/issues/904)) ([ebcfdd1](https://github.com/prebid/salesagent/commit/ebcfdd134afd1e752c43248454203792409d4092))
* Convert advertising channel from single to multi-select ([#897](https://github.com/prebid/salesagent/issues/897)) ([a1aa8e4](https://github.com/prebid/salesagent/commit/a1aa8e42489726f610986d7b1822fe6cd4596968))
* Display sales agent version in agent card ([#902](https://github.com/prebid/salesagent/issues/902)) ([663702b](https://github.com/prebid/salesagent/commit/663702b3095e1b860b9bb20bf569148c993b0f35))
* Implement AI product ranking with simplified catalog ([#906](https://github.com/prebid/salesagent/issues/906)) ([d59e76b](https://github.com/prebid/salesagent/commit/d59e76b5f04cdf820118a84f41131e174fa0efda))
* Simplify user authorization with User records as primary auth method ([#907](https://github.com/prebid/salesagent/issues/907)) ([504b489](https://github.com/prebid/salesagent/commit/504b4897167cce98b05517787904cb3ffeeeaf12))


### Bug Fixes

* Simplify Docker Compose setup to fix mount errors ([#910](https://github.com/prebid/salesagent/issues/910)) ([723b0b2](https://github.com/prebid/salesagent/commit/723b0b2858a27cdd4747be733f2442ac6f7f08de))
* Single-tenant deployment and SSO configuration ([#908](https://github.com/prebid/salesagent/issues/908)) ([e725781](https://github.com/prebid/salesagent/commit/e7257818aa041577f328b1426384fc05df45f96e))
* src.core.format_spec_cache undefined ([#901](https://github.com/prebid/salesagent/issues/901)) ([e3e701c](https://github.com/prebid/salesagent/commit/e3e701c69339402802dd3e5741495047083a41cf))
* Update docs links and fix alembic migrations ([#911](https://github.com/prebid/salesagent/issues/911)) ([e498a43](https://github.com/prebid/salesagent/commit/e498a43139e243bfed91c5fb599ad8f59bd2be69))
* Use pull_request_target for PR title check on fork PRs ([#909](https://github.com/prebid/salesagent/issues/909)) ([bb9817d](https://github.com/prebid/salesagent/commit/bb9817dcdb95d890d79f364b40cff8cf395b3db9))


### Documentation

* Clarify SUPER_ADMIN_EMAILS is optional with per-tenant OIDC ([#905](https://github.com/prebid/salesagent/issues/905)) ([399b255](https://github.com/prebid/salesagent/commit/399b2550dec405a42b9c41f5491b7e9cb67a952d))

## [0.4.1](https://github.com/prebid/salesagent/compare/v0.4.0...v0.4.1) (2025-12-29)


### Documentation

* Add Fly Managed Postgres option to deployment guide ([#894](https://github.com/prebid/salesagent/issues/894)) ([6bf6ce9](https://github.com/prebid/salesagent/commit/6bf6ce91041b372de20513acdd6019b3096a46c1))
* Fix GCP Cloud Run deployment walkthrough ([#896](https://github.com/prebid/salesagent/issues/896)) ([10a9674](https://github.com/prebid/salesagent/commit/10a96743080a6767e1936d41da9cb7845b304f6c))

## [0.4.0](https://github.com/prebid/salesagent/compare/v0.3.0...v0.4.0) (2025-12-28)


### Features

* Add GAM currency detection and Budget Controls integration ([#887](https://github.com/prebid/salesagent/issues/887)) ([f7539e3](https://github.com/prebid/salesagent/commit/f7539e33d77d4fbe589301f9cb095b30a8298a5a))
* Consolidate Docker entrypoint to use Python directly ([#880](https://github.com/prebid/salesagent/issues/880)) ([a12b19d](https://github.com/prebid/salesagent/commit/a12b19dc7e39b0017fa4a7fd90941ff08eebdaf3))
* Default to production setup, make demo mode opt-in ([#883](https://github.com/prebid/salesagent/issues/883)) ([580bcfe](https://github.com/prebid/salesagent/commit/580bcfe90b655f702349c77631c1999355238b65))
* Restrict currency selection to GAM-supported currencies ([#890](https://github.com/prebid/salesagent/issues/890)) ([1076539](https://github.com/prebid/salesagent/commit/10765399d45d1a9705fee38f48ec8ccf76c01c95))


### Bug Fixes

* Only set SESSION_COOKIE_DOMAIN in multi-tenant mode ([#886](https://github.com/prebid/salesagent/issues/886)) ([dfbb577](https://github.com/prebid/salesagent/commit/dfbb577532ef30301613cd2ddf86f3519b483375))


### Code Refactoring

* Reorganize admin settings navigation and elevate publisher management ([#892](https://github.com/prebid/salesagent/issues/892)) ([2f5e9e6](https://github.com/prebid/salesagent/commit/2f5e9e6c638e7bbf0ceca1d9bd3b547b8406fa68))


### Documentation

* Reorganize documentation with automatic link checking ([#879](https://github.com/prebid/salesagent/issues/879)) ([a8f57a6](https://github.com/prebid/salesagent/commit/a8f57a65967214b483aa927603bfdd23341437f2))

## [0.3.0](https://github.com/prebid/salesagent/compare/v0.2.1...v0.3.0) (2025-12-26)


### Features

* Add Docker Hub as secondary container registry ([#878](https://github.com/prebid/salesagent/issues/878)) ([71e7d2f](https://github.com/prebid/salesagent/commit/71e7d2f286b84abe9b875908ffb3a29269731954))
* Enhance AdCP 2.5 creative rotation weight support with improved error handling ([#876](https://github.com/prebid/salesagent/issues/876)) ([d226b58](https://github.com/prebid/salesagent/commit/d226b58e6ad0ea0d694eaca601fe0011f65e2b0b))

## [0.2.1](https://github.com/prebid/salesagent/compare/v0.2.0...v0.2.1) (2025-12-25)


### Bug Fixes

* Use www-data user in nginx-simple.conf for Debian compatibility ([#874](https://github.com/prebid/salesagent/issues/874)) ([81f6e42](https://github.com/prebid/salesagent/commit/81f6e42c4ef239d085a36ca70185e05fe4beb508))

## [0.2.0](https://github.com/prebid/salesagent/compare/v0.1.0...v0.2.0) (2025-12-24)


### Features

* Improve Docker quickstart - ARM64 support, better docs, fail-fast validation ([#859](https://github.com/prebid/salesagent/issues/859)) ([ba3f81a](https://github.com/prebid/salesagent/commit/ba3f81a4e82010ad0d129269fea1086323829cb4))
* Improve single-tenant mode UX and Docker quickstart ([#868](https://github.com/prebid/salesagent/issues/868)) ([8559f8d](https://github.com/prebid/salesagent/commit/8559f8d4cb201f9bc83f744ea2660d9a832bb58a))
* Pydantic AI multi-provider integration with admin UI ([#860](https://github.com/prebid/salesagent/issues/860)) ([1ff0366](https://github.com/prebid/salesagent/commit/1ff03663fdc6514d74869fadc0601b3bd427b6d3))
* show access token directly in advertisers table ([#867](https://github.com/prebid/salesagent/issues/867)) ([ceac7b0](https://github.com/prebid/salesagent/commit/ceac7b070ec6098caa1a26dc58a908a94b484de8))


### Bug Fixes

* enforce tenant human_review_required for media buy approval ([#866](https://github.com/prebid/salesagent/issues/866)) ([92c562e](https://github.com/prebid/salesagent/commit/92c562e8ccd0680a0daf08851a63affa662e74ab))
* Fix/format ids type handling for the format_ids in the products table ([#864](https://github.com/prebid/salesagent/issues/864)) ([bd65beb](https://github.com/prebid/salesagent/commit/bd65beb8763f6ec0ca1af6333031b12fbee2e139))
* Update release-please to use manifest mode (v4 config) ([925a1b2](https://github.com/prebid/salesagent/commit/925a1b2c9bbfc7231049f6da13bed403d9ff13ca))


### Code Refactoring

* align schemas with AdCP library specifications ([#856](https://github.com/prebid/salesagent/issues/856)) ([3c60413](https://github.com/prebid/salesagent/commit/3c6041302cd6921ea3c26bdf960198b3c974d3ad))


### Documentation

* Add Conventional Commits guidance to CLAUDE.md ([4578eab](https://github.com/prebid/salesagent/commit/4578eabdf6f5511c8b0e26bd23a6f9268642e121))
* Add platform-specific deployment guides and Cloud SQL improvements ([#869](https://github.com/prebid/salesagent/issues/869)) ([38626f8](https://github.com/prebid/salesagent/commit/38626f891916c27adb4efd783ee74a23bc8ac86e))
* Update quickstart to use published Docker images ([#857](https://github.com/prebid/salesagent/issues/857)) ([435d6d2](https://github.com/prebid/salesagent/commit/435d6d287a55f3ebae057e5e47a045560bfe66fd))
* Update quickstart to use published Docker images ([#857](https://github.com/prebid/salesagent/issues/857)) ([#861](https://github.com/prebid/salesagent/issues/861)) ([7db5c94](https://github.com/prebid/salesagent/commit/7db5c94331c61d2945a419790e30f01df4cefd05))

## 0.1.0 (2025-12-20)


### ⚠ BREAKING CHANGES

* Media buy creation now FAILS when creatives are missing required fields (URL, dimensions) instead of silently skipping them.

### Features

* Add AdCP 2.5 extension to A2A agent card ([#783](https://github.com/prebid/salesagent/issues/783)) ([a979cb6](https://github.com/prebid/salesagent/commit/a979cb6741395113d5c9e2a79209c53f5e029f8f))
* add auth_header and timeout columns to creative_agents table ([#714](https://github.com/prebid/salesagent/issues/714)) ([64eecd8](https://github.com/prebid/salesagent/commit/64eecd834f0347aeee46b961c2f8730b37da207f))
* add background scheduler to auto-transition media buy statuses based on flight dates ([4af1343](https://github.com/prebid/salesagent/commit/4af13438cbc94f459880e983b9b402cfae621cb9))
* add background scheduler to auto-transition media buy statuses based on flight dates ([d6f8d78](https://github.com/prebid/salesagent/commit/d6f8d787303e3890422face75a406f167d345d60))
* Add brand manifest policy system for flexible product discovery ([#663](https://github.com/prebid/salesagent/issues/663)) ([1c00e1d](https://github.com/prebid/salesagent/commit/1c00e1da7a24bba3b64e20c6534523d336e7815b))
* Add brand manifest policy UI dropdown in Admin ([#726](https://github.com/prebid/salesagent/issues/726)) ([55d2414](https://github.com/prebid/salesagent/commit/55d24145e8641c59baf9fa93330822ebd697910f))
* add commitizen for automated version management ([#666](https://github.com/prebid/salesagent/issues/666)) ([4c49051](https://github.com/prebid/salesagent/commit/4c49051cdea309b2ef20fd5eeb28fd6e3f5890ce))
* Add creative format size filtering with inventory-based suggestions ([#690](https://github.com/prebid/salesagent/issues/690)) ([ced6466](https://github.com/prebid/salesagent/commit/ced64664ff225d1c9c0ca3dcbd5e3a6fc90e473d))
* add date range validation and testing for validation ([9706fd1](https://github.com/prebid/salesagent/commit/9706fd1f0f9d65dd26628cb82986d68595414508))
* Add hierarchical product picker with search and caching ([#707](https://github.com/prebid/salesagent/issues/707)) ([6a6c23d](https://github.com/prebid/salesagent/commit/6a6c23d0a194862f84af4052d9daa58fa2f02183))
* Add inventory profiles for reusable inventory configuration ([#722](https://github.com/prebid/salesagent/issues/722)) ([ceb2363](https://github.com/prebid/salesagent/commit/ceb2363ca7f1879bb3f467d302ee44905194d40d))
* Add manual delivery webhook trigger to admin UI ([f91d55e](https://github.com/prebid/salesagent/commit/f91d55eca789cd01f969eeca521349699bda6713))
* Add manual delivery webhook trigger to admin UI ([e95d6f4](https://github.com/prebid/salesagent/commit/e95d6f4a0b21011e16225104ecd3bc94ba521fe5))
* Add real-time custom targeting values endpoint and visual selector widget ([#678](https://github.com/prebid/salesagent/issues/678)) ([ebd89b9](https://github.com/prebid/salesagent/commit/ebd89b97868e9477ae624010304b417bd5b8d55f))
* Add signals agent registry with unified MCP client ([#621](https://github.com/prebid/salesagent/issues/621)) ([9a15431](https://github.com/prebid/salesagent/commit/9a15431f2a36663e93de4d2a94dcc7f7aef954c6))
* alphabetize targeting keys/values and show display names ([#687](https://github.com/prebid/salesagent/issues/687)) ([c6be06d](https://github.com/prebid/salesagent/commit/c6be06d045bf4a4ff8063044827ef0006c9525dd))
* Auto-download AdCP schemas on workspace startup ([#616](https://github.com/prebid/salesagent/issues/616)) ([94c3876](https://github.com/prebid/salesagent/commit/94c3876ae67bc0759ef823d43e4028d765d28cf1))
* calculate clicks and ctr ([ebe7d66](https://github.com/prebid/salesagent/commit/ebe7d66290a9f5cecff0be783c2d2ff3c376426a))
* enforce strict AdCP v1 spec compliance for Creative model (BREAKING CHANGE) ([#706](https://github.com/prebid/salesagent/issues/706)) ([ff1cbc4](https://github.com/prebid/salesagent/commit/ff1cbc4732e5038b0493cfc90d1e2964de034707))
* improve product workflow - always show formats and descriptive targeting values ([#688](https://github.com/prebid/salesagent/issues/688)) ([4530f25](https://github.com/prebid/salesagent/commit/4530f253d24779aa4ef4f0ee3d527d3258bb28f3))
* Publish Docker images on release ([#855](https://github.com/prebid/salesagent/issues/855)) ([47e88e3](https://github.com/prebid/salesagent/commit/47e88e3fb1a35bd396bb656605d69b9a43d7ba41))
* refactor and add integration and e2e tests for delivery metrics webhooks ([3df36de](https://github.com/prebid/salesagent/commit/3df36dedbab6c53de1bcdf4919403aa69ecc9343))
* refactor webhook deliveries ([f1302ba](https://github.com/prebid/salesagent/commit/f1302ba66be517a999d0e00c78bce16046b6aebb))
* Remove Scope3 dependencies - make codebase vendor-neutral ([#668](https://github.com/prebid/salesagent/issues/668)) ([de503bf](https://github.com/prebid/salesagent/commit/de503bfda0e275cfc2273b93b757c47a9cbccd2c))
* Simplify targeting selector to match existing UI patterns ([#679](https://github.com/prebid/salesagent/issues/679)) ([ce76f8e](https://github.com/prebid/salesagent/commit/ce76f8e2ca01070f3f281aa5f9a69d83789af768))
* support application level context ([#735](https://github.com/prebid/salesagent/issues/735)) ([ea6891d](https://github.com/prebid/salesagent/commit/ea6891d8091f2e178330802293859bf93b3838bc))
* Update budget handling to match AdCP v2.2.0 specification ([#635](https://github.com/prebid/salesagent/issues/635)) ([0a9dd4a](https://github.com/prebid/salesagent/commit/0a9dd4a160deca71508aa83e3e8f5b56b5198e14))


### Bug Fixes

* 'Select All' buttons in Create Product page by fixing JS scope ([5f5553a](https://github.com/prebid/salesagent/commit/5f5553a9e68219300f19cbec891bd16d3e9cea1f))
* 'Select All' buttons in Create Product page by fixing JS scope ([6bcca14](https://github.com/prebid/salesagent/commit/6bcca145aaf8711b7176c94e397fe429276d8bc7))
* Achieve 100% mypy compliance in src/ directory - 881 errors to 0 ([#662](https://github.com/prebid/salesagent/issues/662)) ([d7f4711](https://github.com/prebid/salesagent/commit/d7f47112fa0fe221447bd470d4daeb4783f86b75))
* ad unit format button, targeting selector crash, and service account auth ([#723](https://github.com/prebid/salesagent/issues/723)) ([83bd497](https://github.com/prebid/salesagent/commit/83bd497469eaa30eeba28e3960137fc6ebbbe498))
* AdCP responses now exclude None values in JSON serialization ([#642](https://github.com/prebid/salesagent/issues/642)) ([c3fa69a](https://github.com/prebid/salesagent/commit/c3fa69a511db5942ee307dcad6c1fe5cf6b06246))
* AdCP responses now properly omit null/empty optional fields ([#638](https://github.com/prebid/salesagent/issues/638)) ([ab7c4cd](https://github.com/prebid/salesagent/commit/ab7c4cdaed47c3f3ce85de845914051d3a08197d))
* Add /admin prefix to OAuth redirect URI for nginx routing ([#651](https://github.com/prebid/salesagent/issues/651)) ([a95a534](https://github.com/prebid/salesagent/commit/a95a5344d38667d0e4209dff3f7345d637ed8fbe))
* Add content hash verification to prevent meta file noise ([#659](https://github.com/prebid/salesagent/issues/659)) ([20b0a16](https://github.com/prebid/salesagent/commit/20b0a165b7fea7a8da33840806bc03ef612fc32d))
* add e2e tests for get_media_buy_delivery direct request ([1263a81](https://github.com/prebid/salesagent/commit/1263a8141543b72ac10ef0d8235cddb688a75cf7))
* Add logging + fix targeting browser sync button ([#677](https://github.com/prebid/salesagent/issues/677)) ([bdf19cc](https://github.com/prebid/salesagent/commit/bdf19cccfe177429f0420793ee2eae3206eed157))
* Add missing /api/tenant/&lt;tenant_id&gt;/products endpoint ([9dc4bdc](https://github.com/prebid/salesagent/commit/9dc4bdcf3787a40a921d1c5374a2f3da1776c0fb))
* Add missing activity feed and audit logs to manual approval path ([#729](https://github.com/prebid/salesagent/issues/729)) ([114778c](https://github.com/prebid/salesagent/commit/114778c85d009333d30b7640b623a11bd8ee0d6f))
* Add missing adapter_type to SyncJob creation ([fb0fb79](https://github.com/prebid/salesagent/commit/fb0fb7905699503087180af91acf8190c2fa4bfa))
* Add null safety checks for audience.type and audience.segment_type ([#682](https://github.com/prebid/salesagent/issues/682)) ([b8e6e77](https://github.com/prebid/salesagent/commit/b8e6e77a4aea4a2589e7e1fddc73f6346e2729c2))
* add pricing to delivery ([78eab1e](https://github.com/prebid/salesagent/commit/78eab1e05a60e1ac86cdb340c7ec0708078d33bb))
* Add timeout to discover_ad_units to prevent stuck syncs ([56457ad](https://github.com/prebid/salesagent/commit/56457ad07c329064b451869b2e25134a401bb0d3))
* add type field to audience segments API for filtering ([28302f2](https://github.com/prebid/salesagent/commit/28302f27964287bdacee4261d97b4ecc7467de11))
* add type field to audience segments API for filtering ([474df9a](https://github.com/prebid/salesagent/commit/474df9a4545d0ded0873d22303fe8bba4824d59f))
* advertiser creation ([4e9e32d](https://github.com/prebid/salesagent/commit/4e9e32d35e0a65e57c5b1c218a7c38e8dee06a83))
* advertiser creation ([d323477](https://github.com/prebid/salesagent/commit/d323477dc424eef8655a76d5fa43e9c6f3ad644b))
* apply type filter when fetching inventory by IDs ([3fc3ded](https://github.com/prebid/salesagent/commit/3fc3ded211a5137c932fbd20be18b36a35a19e46))
* approval flow ([ee2e90a](https://github.com/prebid/salesagent/commit/ee2e90acfb204478b1c1bcc5c52e07ee97e78cce))
* attempt to fix e2e test in ci ([8c269a8](https://github.com/prebid/salesagent/commit/8c269a8ffa56752365f5ebf113253f5ce6ded7fc))
* Auto-create default principal and improve setup output ([#849](https://github.com/prebid/salesagent/issues/849)) ([0c222f3](https://github.com/prebid/salesagent/commit/0c222f3afdad4bf4358e3987b04d2bd64ce517d7))
* Auto-create user records for authorized emails on tenant login ([#492](https://github.com/prebid/salesagent/issues/492)) ([454eb8f](https://github.com/prebid/salesagent/commit/454eb8ffbb015b63e958f86d17361c0462358b32))
* Check super admin status before signup flow redirect ([#674](https://github.com/prebid/salesagent/issues/674)) ([e5dfb8d](https://github.com/prebid/salesagent/commit/e5dfb8dc4c98bf426f463f01992b31aab9bab3de))
* Clean up smoke tests and resolve warnings ([#629](https://github.com/prebid/salesagent/issues/629)) ([73cbc99](https://github.com/prebid/salesagent/commit/73cbc99d4ed8c8385b0b09b0ce5e43fa7ecc006b))
* Complete /admin prefix handling for all API calls ([#736](https://github.com/prebid/salesagent/issues/736)) ([4c20c9c](https://github.com/prebid/salesagent/commit/4c20c9c6e68d953f1548fe2253338b4d67dc18e1))
* Convert FormatReference to FormatId in MediaPackage reconstruction ([#656](https://github.com/prebid/salesagent/issues/656)) ([7c24705](https://github.com/prebid/salesagent/commit/7c247053d94abbce15331b4df05069636ad1409f))
* Convert summary dict to JSON string in sync completion ([3318ee0](https://github.com/prebid/salesagent/commit/3318ee0bed23bb1a21d2f2cb8870d73d59234dac))
* convert to utc ([bcb54f0](https://github.com/prebid/salesagent/commit/bcb54f01bba60ac6862332942d09ee332387b3a5))
* Correct AdManagerClient signature for service account auth ([#571](https://github.com/prebid/salesagent/issues/571)) ([bcb1686](https://github.com/prebid/salesagent/commit/bcb1686fa8c23492db73a63e87d088f5ae6c6246)), closes [#570](https://github.com/prebid/salesagent/issues/570)
* Correct API field name mismatch in targeting selector widget ([#681](https://github.com/prebid/salesagent/issues/681)) ([9573749](https://github.com/prebid/salesagent/commit/9573749beb05d260b0786479c68b479c85807c56))
* correct creative agent URL typo (creatives → creative) ([#844](https://github.com/prebid/salesagent/issues/844)) ([f29659b](https://github.com/prebid/salesagent/commit/f29659bbe65b2f3e161a95f44749fb89b348390e))
* correct inventory search endpoint and parameters in unified view ([201fd4f](https://github.com/prebid/salesagent/commit/201fd4fdc90ffc6cb572275b64fae03d4dda4b26))
* correct inventory search endpoint and parameters in unified view ([5532adb](https://github.com/prebid/salesagent/commit/5532adb5d2f6e5332aa3db3fb90029aefc0f551e))
* Correct tenant context ordering in update_media_buy ([#773](https://github.com/prebid/salesagent/issues/773)) ([2c2d9b1](https://github.com/prebid/salesagent/commit/2c2d9b171df6db044f652d81a927baff2977e108))
* Create mock properties only for mock adapters ([#854](https://github.com/prebid/salesagent/issues/854)) ([efdcfca](https://github.com/prebid/salesagent/commit/efdcfcad626d61b1b76ef96979d4ed3d8a5ec47a))
* creative agent url check; allow to fallback to /mcp when creating mcp client ([09bc1ac](https://github.com/prebid/salesagent/commit/09bc1ac6782faf1362ba253f23785c842aa771d7))
* creative agent url check; allow to fallback to /mcp when creating mcp client ([6bf221f](https://github.com/prebid/salesagent/commit/6bf221f501fb6f700d2092bf83cca58884deb365))
* creative approval/rejection webhook delivery ([9062449](https://github.com/prebid/salesagent/commit/9062449959bfcca02f1d3377b5f9f8c962917d57))
* Creative management - reject invalid creatives ([#460](https://github.com/prebid/salesagent/issues/460)) ([1540de3](https://github.com/prebid/salesagent/commit/1540de3946f6de9b22fd37e9b08077f006c86894))
* Default publisher_properties to 'all' when not specified ([#759](https://github.com/prebid/salesagent/issues/759)) ([690f2b1](https://github.com/prebid/salesagent/commit/690f2b12274871f3339432a23301f541f93e863e))
* display and save custom targeting keys in product inventory ([#692](https://github.com/prebid/salesagent/issues/692)) ([991656b](https://github.com/prebid/salesagent/commit/991656b31702016d744a6e1bda75674a24b4fee8))
* Docker test cleanup to prevent 100GB+ resource accumulation ([9036cae](https://github.com/prebid/salesagent/commit/9036cae83ccd3d930582cd79f11db629e8b5b4df))
* Docker test cleanup to prevent 100GB+ resource accumulation ([9ed12fd](https://github.com/prebid/salesagent/commit/9ed12fdf33ede9aed33e692894a0ea65387f2d32))
* e2e test context initialization ([0c463a1](https://github.com/prebid/salesagent/commit/0c463a16195a39ccc64ecc526856174f74382ec0))
* e2e test for media buy deliveries webhooks ([64d9529](https://github.com/prebid/salesagent/commit/64d95292edb55ff16ce993cbf20a25468fb4765e))
* edit configuration feature ([fb61f20](https://github.com/prebid/salesagent/commit/fb61f204ba66d64e5a734d81013ba0be4b5a4f7b))
* Enable all 189 integration_v2 tests - achieve 100% coverage goal ([#626](https://github.com/prebid/salesagent/issues/626)) ([6377462](https://github.com/prebid/salesagent/commit/6377462815745643b24d8c40058824261e6d863f))
* enforce brand_manifest_policy in get_products ([#731](https://github.com/prebid/salesagent/issues/731)) ([075e681](https://github.com/prebid/salesagent/commit/075e6811251861849002c557b78ab9ec251eb5d2))
* Ensure Package objects always have valid status ([#755](https://github.com/prebid/salesagent/issues/755)) ([757c0d3](https://github.com/prebid/salesagent/commit/757c0d320141c840a4861bc516b51b6263a44f0e))
* ensure User record creation during OAuth tenant selection ([#701](https://github.com/prebid/salesagent/issues/701)) ([be22ffb](https://github.com/prebid/salesagent/commit/be22ffb675032fe26610fc037b50e32620de7700))
* Exclude null values from list_authorized_properties response ([#647](https://github.com/prebid/salesagent/issues/647)) ([5afb6b5](https://github.com/prebid/salesagent/commit/5afb6b5a0544e117da8ce1a439d40a36eb0fe629))
* existing unit tests ([60a1961](https://github.com/prebid/salesagent/commit/60a1961ec1e1192d1ce85dbcabc6fadc4e409df9))
* fetch inventory by IDs to bypass 500-item API limit ([c1e197e](https://github.com/prebid/salesagent/commit/c1e197eb6d1882c317ef96b13de5d7b4dcf42418))
* fetch specific ad units by ID for placement size extraction ([85f792d](https://github.com/prebid/salesagent/commit/85f792ded5a47a2d1de0cbf351ef1eccbc31b590))
* file lint error ([#625](https://github.com/prebid/salesagent/issues/625)) ([2fec26e](https://github.com/prebid/salesagent/commit/2fec26eaf3cd51faa98100264a80d87c8c437980))
* flush deleted inventory mappings before recreating ([c83e34c](https://github.com/prebid/salesagent/commit/c83e34c8aa1712b0ec4c0f386554595f9f134255))
* GAM adapter ([f4f0df1](https://github.com/prebid/salesagent/commit/f4f0df1bc33edd4d37e1d800ba07a66df6e92c55))
* GAM adpaters and other logic changes including bumping adcp client to 2.5.5 ([8367e0a](https://github.com/prebid/salesagent/commit/8367e0a1f9d52e04ce41f81cb35bfd91c33fbcdc))
* GAM advertiser search and pagination with Select2 UI ([#710](https://github.com/prebid/salesagent/issues/710)) ([792d4ae](https://github.com/prebid/salesagent/commit/792d4ae31a27452e8043ae6b4e9baa493c9e37a5))
* GAM product placements not saving when line_item_type absent ([#691](https://github.com/prebid/salesagent/issues/691)) ([eb66e33](https://github.com/prebid/salesagent/commit/eb66e3313c9dd0fbbdfe8ff7c0b6674463e2bdd2))
* GAM test connection error fix ([78e88ae](https://github.com/prebid/salesagent/commit/78e88aeb4d05bf0ebf852a1ad2494dbc5f1c2404))
* GAM test error fix ([48b07a9](https://github.com/prebid/salesagent/commit/48b07a9c14850ca398c78b3102206b4ba09133f1))
* Handle /admin prefix in login redirects and API calls ([#733](https://github.com/prebid/salesagent/issues/733)) ([15ab582](https://github.com/prebid/salesagent/commit/15ab582e94dfdc7ed5b318bf4d2dec91b517551e))
* Handle CreateMediaBuyError response in approval and main flows ([#745](https://github.com/prebid/salesagent/issues/745)) ([574943b](https://github.com/prebid/salesagent/commit/574943b88ff076fbb0d2b9d932cde49a96e2e497))
* Handle unrestricted agents in property discovery (no property_ids = all properties) ([#750](https://github.com/prebid/salesagent/issues/750)) ([136575b](https://github.com/prebid/salesagent/commit/136575b6dcebaaea0782f9a0edf263126881daa2))
* Implement creative assignment in update_media_buy ([#560](https://github.com/prebid/salesagent/issues/560)) ([99cdcdc](https://github.com/prebid/salesagent/commit/99cdcdc741be6e103e8db3dcefa36854a63facc8))
* implement missing naming template preview logic ([39eafff](https://github.com/prebid/salesagent/commit/39eafffc6803ea51fc539c9c2bd6ed768a43aefa))
* implement missing naming template preview logic ([66fc55d](https://github.com/prebid/salesagent/commit/66fc55d0d646810454b9148128d2159f363b7d19))
* Implement missing update_media_buy field persistence ([#749](https://github.com/prebid/salesagent/issues/749)) ([f67a304](https://github.com/prebid/salesagent/commit/f67a304690067608eda74c796cf2deff4d0448d6))
* Import get_testing_context in list_authorized_properties ([#632](https://github.com/prebid/salesagent/issues/632)) ([6612c7d](https://github.com/prebid/salesagent/commit/6612c7d1870bdcf05b328452c10e44796c35a92c))
* improve creative status handling and dashboard visibility ([#711](https://github.com/prebid/salesagent/issues/711)) ([539e1bb](https://github.com/prebid/salesagent/commit/539e1bbb926c92e390a1a97529db5640b17134d0))
* improve inventory browser UX and fix search lag ([#709](https://github.com/prebid/salesagent/issues/709)) ([0d09f1b](https://github.com/prebid/salesagent/commit/0d09f1bcbc024acc13a7cdab3df2e105ec18a92a))
* include ALL statuses when fetching inventory names for existing products ([2a61600](https://github.com/prebid/salesagent/commit/2a616008f2d903c550e4d3e3e5e5c8fb5271f91d))
* Include service_account_email in adapter_config dict for template ([#517](https://github.com/prebid/salesagent/issues/517)) ([c36aef6](https://github.com/prebid/salesagent/commit/c36aef618c21720e2399dff996fa10f6f7d98bd2))
* increase sync_id length from 50 to 100 ([cd89098](https://github.com/prebid/salesagent/commit/cd890988e0ccc8d570c94c1b8addd818d075e2f2))
* increase sync_id length from 50 to 100 ([6ae87ff](https://github.com/prebid/salesagent/commit/6ae87ff9798ec4050d0ddaf96a1d75fd7a5522dd))
* Integration tests, mypy errors, and AdCP schema compliance ([#633](https://github.com/prebid/salesagent/issues/633)) ([77c4da6](https://github.com/prebid/salesagent/commit/77c4da632b35b806452b89bdafd1bce781699fff))
* Integration tests, mypy errors, and deprecation warnings ([#628](https://github.com/prebid/salesagent/issues/628)) ([be52151](https://github.com/prebid/salesagent/commit/be521514a146ae765c879f7ad3b84d4c9358462e))
* Integration tests, mypy errors, and test infrastructure improvements ([#631](https://github.com/prebid/salesagent/issues/631)) ([ca4c184](https://github.com/prebid/salesagent/commit/ca4c1846d38a95442d1ec7d89710a2a8ffdf5d6d))
* inventory profile save URL and property_mode handling ([40f192a](https://github.com/prebid/salesagent/commit/40f192a8351143d3d92f63a62944032ab0019ac9))
* inventory profile save URL and property_mode handling ([7440350](https://github.com/prebid/salesagent/commit/7440350d323443e3e7a16dc1149b0b19ec1b0f34))
* inventory sync ([d300258](https://github.com/prebid/salesagent/commit/d300258260bd64f7aaaf75f0d1c359380783f153))
* Inventory sync JavaScript errors ([0d2ad1f](https://github.com/prebid/salesagent/commit/0d2ad1ff915a30849534eaf66318518166a49edc))
* inventory sync status now checks GAMInventory table instead of Products ([#708](https://github.com/prebid/salesagent/issues/708)) ([193e87d](https://github.com/prebid/salesagent/commit/193e87d0cf3c4ca0cab1d5edc16911a0def1711b))
* lint errors ([dff427a](https://github.com/prebid/salesagent/commit/dff427a546a52933b9d9a05899b8ccd1abfa3fc6))
* list_tasks query using non-existent WorkflowStep.tenant_id ([#822](https://github.com/prebid/salesagent/issues/822)) ([c17abcb](https://github.com/prebid/salesagent/commit/c17abcb1d2d6a91577f2cf99f4df690131670f8b))
* Load pricing_options when querying products ([#413](https://github.com/prebid/salesagent/issues/413)) ([a87c69a](https://github.com/prebid/salesagent/commit/a87c69aee9568835cd599d3de7754f6c632c696e))
* make media_buy_ids optional in get_media_buy_delivery per AdCP spec ([#704](https://github.com/prebid/salesagent/issues/704)) ([5c69013](https://github.com/prebid/salesagent/commit/5c690131d9d90a59acc47e10954768adf9456cff))
* media buy tests creation ([4045386](https://github.com/prebid/salesagent/commit/4045386a4e5f498f087219197dfc9a266e5176be))
* media buys & creatives ([58c4f45](https://github.com/prebid/salesagent/commit/58c4f45901abfaa3458336c23ec69e5c569efe7d))
* mypy ([77b5ecc](https://github.com/prebid/salesagent/commit/77b5ecc2fd215ba7761dcd9437f1049a497ca3ac))
* nest inventory picker modal to resolve search input focus issue ([a14c47b](https://github.com/prebid/salesagent/commit/a14c47b835252303339eb3d4ca4c2da1060c2e99))
* nest inventory picker modal to resolve search input focus issue ([f888fe9](https://github.com/prebid/salesagent/commit/f888fe93ad6cd7b733e663bb7414204ff9e835d3))
* Normalize agent URL variations for consistent validation ([#497](https://github.com/prebid/salesagent/issues/497)) ([9bef942](https://github.com/prebid/salesagent/commit/9bef94207b271f9436347536c1df4dc5ba9f0f8c))
* parse and apply custom targeting from product forms to GAM line items ([#686](https://github.com/prebid/salesagent/issues/686)) ([a1132ae](https://github.com/prebid/salesagent/commit/a1132aef30c7bdf8fb1ceefee8721217c4f31aef))
* pass DELIVERY_WEBhOOK_INTERVAL when running e2e tests in ci/cd ([07f3eee](https://github.com/prebid/salesagent/commit/07f3eee4ee8b2baa67c1cb55c63df014c7fad1be))
* persist targeting and placement selections in product editor ([#689](https://github.com/prebid/salesagent/issues/689)) ([ebbecf0](https://github.com/prebid/salesagent/commit/ebbecf047e56b3ea6004d5721f23421b029c4363))
* populate custom targeting keys when editing products ([#693](https://github.com/prebid/salesagent/issues/693)) ([88f0b9e](https://github.com/prebid/salesagent/commit/88f0b9ea6df0f1507638d7f46674e7c1dd7b3f45))
* prevent duplicate IDs in placement display after removal ([#696](https://github.com/prebid/salesagent/issues/696)) ([87b0eac](https://github.com/prebid/salesagent/commit/87b0eac31f4f2b788f6c01e4ad6887a2fa30fcf3))
* Prevent duplicate tenant display when user has both domain and email access ([#660](https://github.com/prebid/salesagent/issues/660)) ([92ca049](https://github.com/prebid/salesagent/commit/92ca049e0d34c77d0473430f50129bbbaedc2553))
* product editor bugs - JSON parsing, text color, selection preservation ([#694](https://github.com/prebid/salesagent/issues/694)) ([50765cf](https://github.com/prebid/salesagent/commit/50765cfd83b581a4e7141dd7e837e6a57ff48bae))
* rebase ([581b18b](https://github.com/prebid/salesagent/commit/581b18b4a49bc811329534dcde1f0d3b81ce2f76))
* Reduce skipped tests from 323 to ~97 (70% improvement) ([#669](https://github.com/prebid/salesagent/issues/669)) ([c48f978](https://github.com/prebid/salesagent/commit/c48f978f427d17b3092261d67d823fff18093d61))
* rejection ([79cb754](https://github.com/prebid/salesagent/commit/79cb754c6240dd8370a73642bdf8f6caa5f5aca8))
* remove /a2a suffix from A2A endpoint URLs and add name field to configs ([2b036c6](https://github.com/prebid/salesagent/commit/2b036c6fc44a3316d15e82c0245d70d447b7142c))
* remove /a2a suffix from A2A endpoint URLs and add name field to configs ([13914b8](https://github.com/prebid/salesagent/commit/13914b8584dea3d17c8e751ad7d7db58c2b3e2b2))
* remove 97% of type: ignore comments and fix 169 mypy errors ([#820](https://github.com/prebid/salesagent/issues/820)) ([#823](https://github.com/prebid/salesagent/issues/823)) ([1175c63](https://github.com/prebid/salesagent/commit/1175c631a833fcd1f888bfc98e8949cecad6ece9))
* Remove auto-restart of delivery simulators on server boot ([#646](https://github.com/prebid/salesagent/issues/646)) ([52c2378](https://github.com/prebid/salesagent/commit/52c2378d20620a2ab55f125d6a0f87ead73ccb02))
* remove dead API docs link and fix testing docs path ([#700](https://github.com/prebid/salesagent/issues/700)) ([9fd959e](https://github.com/prebid/salesagent/commit/9fd959eed4c98a9d6ddb7f3fbb5abbba02cc99a7)), closes [#676](https://github.com/prebid/salesagent/issues/676)
* Remove fake media_buy_id from pending/async responses in mock adapter ([#658](https://github.com/prebid/salesagent/issues/658)) ([dc2a2ba](https://github.com/prebid/salesagent/commit/dc2a2ba63dca42e36f0d6b6cae6a9d23c22468cb))
* remove inventory sync requirement for mock adapter ([#719](https://github.com/prebid/salesagent/issues/719)) ([4268b2e](https://github.com/prebid/salesagent/commit/4268b2e9a93a499ec6b03518b8c3c3fd42361568))
* Remove non-existent fields from SyncCreativesResponse ([9bf3da7](https://github.com/prebid/salesagent/commit/9bf3da7b358d55739e9687d50b0a62f0a7d5ce22))
* Remove non-existent fields from SyncCreativesResponse ([453c329](https://github.com/prebid/salesagent/commit/453c329b40899fdcaea9bffc1fc766875a1b963b))
* Remove non-existent impressions field from AdCPPackageUpdate ([#500](https://github.com/prebid/salesagent/issues/500)) ([404c653](https://github.com/prebid/salesagent/commit/404c6539b7a915b1df47ea797bd181c70aac6312))
* Remove non-spec tags field from ListAuthorizedPropertiesResponse ([#643](https://github.com/prebid/salesagent/issues/643)) ([a38b3d7](https://github.com/prebid/salesagent/commit/a38b3d751ecb3bf55983020ec52d08a4fc20053c))
* Remove stale ui-test-assistant MCP server configuration ([#851](https://github.com/prebid/salesagent/issues/851)) ([0e7cf9a](https://github.com/prebid/salesagent/commit/0e7cf9aba2879fe336ff8f1c7f4872e1e70c9f6d))
* remove top-level budget requirement from create_media_buy ([#725](https://github.com/prebid/salesagent/issues/725)) ([4474de3](https://github.com/prebid/salesagent/commit/4474de3d1cf724c6dddc6b0bc77c999015e1acd3))
* Replace progress_data with progress in SyncJob ([f4008f4](https://github.com/prebid/salesagent/commit/f4008f430fddc6acb1822ac9c68875e17bc5c99c))
* require authentication for sync_creatives and update_media_buy ([#721](https://github.com/prebid/salesagent/issues/721)) ([defa383](https://github.com/prebid/salesagent/commit/defa3837a52bede3635a3d1d3f74eb0e84c37972))
* Resolve GAM inventory sync and targeting data loading issues ([#675](https://github.com/prebid/salesagent/issues/675)) ([ca31c6a](https://github.com/prebid/salesagent/commit/ca31c6a6334d0db9afa3beadefdfb5d77429f503))
* Resolve product creation and format URL issues ([#756](https://github.com/prebid/salesagent/issues/756)) ([d99a6f8](https://github.com/prebid/salesagent/commit/d99a6f83416d39864e43193eb5db07a4e6595463))
* Restore accidentally deleted commitizen configuration files ([c92075c](https://github.com/prebid/salesagent/commit/c92075c8c9d2602484cb3153fdbbd5460e4fa0f2))
* Restore brand manifest policy migrations and merge with signals agent ([e30c106](https://github.com/prebid/salesagent/commit/e30c106c9517fa342a06ca0ace829b63780532a9))
* restore unrelative changes ([8c159e6](https://github.com/prebid/salesagent/commit/8c159e659ac7f01e252de4ee8c44654718add4e6))
* Return human-readable text in MCP protocol messages ([#644](https://github.com/prebid/salesagent/issues/644)) ([3bb9bce](https://github.com/prebid/salesagent/commit/3bb9bcedef3d9d19e3564f76847468ced02bf812))
* Route external domains to tenant login instead of signup ([#661](https://github.com/prebid/salesagent/issues/661)) ([b194b83](https://github.com/prebid/salesagent/commit/b194b83757250efce28f07da7496ef681a18a73f))
* sales agent logic ([0a51476](https://github.com/prebid/salesagent/commit/0a51476a9411f7f31d7daa495322b071bda91ca3))
* sanitize tenant ID in GCP service account creation ([b4c3bbc](https://github.com/prebid/salesagent/commit/b4c3bbc7b221d93a5f9ad5fa6495f9ae82dba338))
* sanitize tenant ID in GCP service account creation ([6774587](https://github.com/prebid/salesagent/commit/67745871f9c52700de3ad522ce45e6a415c31e5c))
* Set session role for super admin OAuth login ([#654](https://github.com/prebid/salesagent/issues/654)) ([505b24f](https://github.com/prebid/salesagent/commit/505b24f45a2d9cf573e8726ea011f51cba7a1c27))
* set tenant context before fetching delivery metrics ([1042274](https://github.com/prebid/salesagent/commit/10422746dc85d063615b6a1c67cc96a31734866e))
* Set tenant context when x-adcp-tenant header provides direct tenant_id ([#467](https://github.com/prebid/salesagent/issues/467)) ([20b3f9c](https://github.com/prebid/salesagent/commit/20b3f9c88171643ed8e8f0117029fb94eb63ff41))
* show both name and ID for placements consistently ([#695](https://github.com/prebid/salesagent/issues/695)) ([52caddd](https://github.com/prebid/salesagent/commit/52caddd69f0785fd9cd2a8b7d1c9e742c3766f47))
* signals agent test endpoint async handling ([#718](https://github.com/prebid/salesagent/issues/718)) ([e1c5d72](https://github.com/prebid/salesagent/commit/e1c5d722db002c22d16ad28f6f272b2aafa08359))
* Support ListCreativesRequest convenience fields with adcp 2.9.0 ([#770](https://github.com/prebid/salesagent/issues/770)) ([1bd57f0](https://github.com/prebid/salesagent/commit/1bd57f0fd8179c6fb7eacfea079da60ae06752d7))
* syntax ([af504a6](https://github.com/prebid/salesagent/commit/af504a690ad2ad4da7a660308a089869969a97f6))
* Targeting browser, product page auth, UI repositioning + format conversion tests ([#683](https://github.com/prebid/salesagent/issues/683)) ([d363627](https://github.com/prebid/salesagent/commit/d3636275cbf5b1ac2aae50fa91639b221993a38c))
* targeting keys errors in browser and product pages ([#685](https://github.com/prebid/salesagent/issues/685)) ([7fc3603](https://github.com/prebid/salesagent/commit/7fc3603c63f9d0a870b5b36fd86763bcb277dfb7))
* test ([62c2fe0](https://github.com/prebid/salesagent/commit/62c2fe0bca0fd7416770689929986385f10d52a2))
* test delivery webhook sends for fresh data ([b35457f](https://github.com/prebid/salesagent/commit/b35457f4746eac673d5c64f4d4f7a3fa10501262))
* test scase in test_format_conversion_approval ([3060a24](https://github.com/prebid/salesagent/commit/3060a243664408ee26ef2cb4fcd90638022f3389))
* tests ([5d5347c](https://github.com/prebid/salesagent/commit/5d5347ce8502893a606bfa3778c8ee6d4e541a77))
* tests ([1b1ce8e](https://github.com/prebid/salesagent/commit/1b1ce8e3f29ef0592efe09692ac98e06cdd6c8fb))
* tests ([f70f684](https://github.com/prebid/salesagent/commit/f70f6845447bd920fed134d68d736fa1f818b131))
* tests ([c966e43](https://github.com/prebid/salesagent/commit/c966e43d21987bae837bb5eac19c52ee95122f54))
* try to pass delivery interval through docker-compose.override.yml for e2e tests ([c830255](https://github.com/prebid/salesagent/commit/c83025543430ebefab6260b8000a57c8f7cd39fd))
* types ([d545f14](https://github.com/prebid/salesagent/commit/d545f14bf8cb165b8de0617f24360571aceff09a))
* typo in integration test ([d09125e](https://github.com/prebid/salesagent/commit/d09125eeec4c5e39c8010b67a781162d37f727a3))
* Unskip 3 integration tests and reduce mypy errors by 330 ([#627](https://github.com/prebid/salesagent/issues/627)) ([37cc165](https://github.com/prebid/salesagent/commit/37cc1656a3ffd192dd127d68aff7cc1194b86bed))
* Update DNS widget to use A record pointing to Approximated proxy IP ([#636](https://github.com/prebid/salesagent/issues/636)) ([3291ae6](https://github.com/prebid/salesagent/commit/3291ae684174cc8d2d6de4188a384fc18b9ddeb2))
* Update tenant selector template to work with dictionary objects ([#652](https://github.com/prebid/salesagent/issues/652)) ([aa612a3](https://github.com/prebid/salesagent/commit/aa612a35aae011f638ed906ac2c71b0a50d3757d))
* Use content-based hashing for schema sync to avoid metadata noise ([#649](https://github.com/prebid/salesagent/issues/649)) ([5625955](https://github.com/prebid/salesagent/commit/5625955d913bb6ea4264c04d0ba9d4767f9a57fd))
* use correct field name inventory_metadata in IDs path ([4e7d7a2](https://github.com/prebid/salesagent/commit/4e7d7a2344d2553e9396ff53de7031fcf7e9873b))
* Use SQLAlchemy event listener for statement_timeout with PgBouncer ([#641](https://github.com/prebid/salesagent/issues/641)) ([bde8186](https://github.com/prebid/salesagent/commit/bde8186e1d182cd0279b1e0c772fb79fa09654ea))
* wrap service account credentials with GoogleCredentialsClient ([#727](https://github.com/prebid/salesagent/issues/727)) ([9d21709](https://github.com/prebid/salesagent/commit/9d2170948c9efd844b4f1a7ef658935860947351))


### Documentation

* clarify GAM setup with three clear paths and environment validation ([#847](https://github.com/prebid/salesagent/issues/847)) ([6a2e951](https://github.com/prebid/salesagent/commit/6a2e95143bc736795df1bd83a87e421024d182d3))
* document PYTHONPATH requirement for Docker hot reload ([#846](https://github.com/prebid/salesagent/issues/846)) ([03878f4](https://github.com/prebid/salesagent/commit/03878f46de5132430e561f032edbd7070d3dbe5c))

## [Unreleased]

### Added
- Changeset system for automated version management
- CI workflows to enforce changeset requirements on PRs
- Automated version bump PR creation when changesets are merged

## [0.1.0] - 2025-01-29

Initial release of the Prebid Sales Agent reference implementation.

### Added
- MCP server implementation with AdCP v2.3 support
- A2A (Agent-to-Agent) protocol support
- Multi-tenant architecture with PostgreSQL
- Google Ad Manager (GAM) adapter
- Mock ad server adapter for testing
- Admin UI with Google OAuth authentication
- Comprehensive testing backend with dry-run support
- Real-time activity dashboard with SSE
- Workflow management system
- Creative management and approval workflows
- Audit logging
- Docker deployment support
- Extensive documentation

[Unreleased]: https://github.com/prebid/salesagent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/prebid/salesagent/releases/tag/v0.1.0
