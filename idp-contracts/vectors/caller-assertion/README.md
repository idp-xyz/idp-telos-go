# 调用者断言的一致性向量（`v1.json`）

网关给转发的调用者签名（idp-mcp ADR-0001 D44），上游验签（idp-sandbox D18、idp-web D2）。签名方和两个验签方各有一份代码，规则必须一致。这个文件是它们共用的判例：同一批请求、同一批断言、同一个期望结果。

| 谁 | 做什么 | 副本 |
|---|---|---|
| idp-mcp | 生成这个文件（用真实的 `CallerSigner`），并用自己的代码检查：每条网关签的断言的签名、头、有效期，以及 `req` 是否等于 `RequestDigest` | `internal/gateway/testdata/caller-assertion-v1.json` |
| idp-sandbox | 把每个用例交给真实的 `CallerVerifier` 和服务器的 `tools/call` | `internal/mcp/testdata/caller-assertion-v1.json` |
| idp-web | 同上 | `internal/mcp/testdata/caller-assertion-v1.json` |

`tools/validate.py` 检查三份副本与这里的发布文件逐字节相同（对应的仓库不在本机时报告"未检查"，不算失败）。**四份文件只能一起改**：先在 idp-mcp 重新生成，再把同一份复制到另外三处。

## 重新生成

```bash
cd idp-mcp && go test ./internal/gateway -run TestCallerVectors -update
cp internal/gateway/testdata/caller-assertion-v1.json ../idp-contracts/vectors/caller-assertion/v1.json
cp internal/gateway/testdata/caller-assertion-v1.json ../idp-sandbox/internal/mcp/testdata/
cp internal/gateway/testdata/caller-assertion-v1.json ../idp-web/internal/mcp/testdata/
```

不带 `-update` 时，同一个测试只检查已提交的文件。每次生成都用新的密钥对：**私钥只在生成时的进程里，不写入任何地方**，所以文件里只有公钥，也没有人能在事后再签一条向量。因为密钥和签名每次都变，重新生成会改动整个文件；除非规则变了、要加用例，不要重新生成。

## 格式

```json
{
  "format": "idp.telos.caller-assertion-vectors/v1",
  "generatedBy": "…",
  "audience": "http://sandbox:8570/mcp",
  "keys": [ { "name": "gateway", "kid": "…", "pem": "-----BEGIN PUBLIC KEY-----…" } ],
  "cases": [ { "name": "…", "description": "…", "origin": "gateway", "steps": [ … ] } ]
}
```

- `audience`：验签方被登记的端点，即断言的 `aud` 应当等于的值。
- `keys`：验签方持有的网关公钥（PKIX，P-256，PEM），全部给验签方（轮换期间可以有几把）。`kid` 是 RFC 7638 指纹，供核对。
- `origin`：`gateway` 表示断言由 idp-mcp 的真实签名代码生成；`forged` 表示由生成测试里的测试代码构造（真实的签名方做不出来的东西：5 分钟有效期、错误的算法、伪造的签名……）。两者对验签方没有区别。

### 用例与步骤

每个用例用一个**全新的**验签方（新的 `jti` 表）。`steps` 按顺序执行，**同一个用例里的步骤共用同一个验签方**，这是表达重放的方式：第一步被接受，第二步出示同一个断言，被拒绝。

一个步骤：

| 字段 | 含义 |
|---|---|
| `method` | 验签方此时处理的 JSON-RPC 方法，如 `tools/call` |
| `params` | 收到的 `params`，原样，包含 `_meta`；断言在 `params._meta["io.idp.telos/caller-assertion"]`（字符串），没有这个键就是没有断言。`_meta` 里还有一份没签名的 `io.idp.telos/caller`，验签方不看它 |
| `now` | 求值时的时间（RFC 3339，UTC）。验签方用它代替系统时钟，过期类用例因此是确定的 |
| `expect` | 期望的结果，见下 |
| `signedRequest` | 可选，只给生成方看：断言签的是哪个请求，当它不是出示的这个（改了参数、换了方法）。验签方忽略 |

`expect`：

- `{"outcome": "accept", "caller": {"subject", "tenant", "roles", "client", "actor"}}`：接受，并且得到这个调用者；`roles` 总是列表（没有就是空表），`client`、`actor` 没有时是空串。
- `{"outcome": "refuse", "reason": "<关键字>"}`：拒绝，原因是下表的一个。关键字说的是哪条规则拒绝的，不是错误信息；各验签方把它对应到自己的错误上。一个断言同时违反几条规则时，向量里只有一条会先被判（用例已经避免了这种含糊）。

| `reason` | 规则 |
|---|---|
| `no_assertion` | 没有断言 |
| `malformed` | 不是紧凑 JWS（段数、base64url、声明不是对象），或超过 8192 字节 |
| `header` | `alg` 不是 `ES256`（含 `none`、`HS256`、`ES384`），或 `typ` 不是 `idp-caller+jwt` |
| `unknown_key` | `kid` 不是持有的公钥 |
| `bad_signature` | 签名对不上（含长度不是 64 字节） |
| `audience` | `aud`（字符串或列表）不包含自己的端点 |
| `expired` | 没有 `exp`，或 `now` 超过 `exp` 加 30 秒 |
| `issued_in_future` | 没有 `iat`，或 `iat` 超过 `now` 加 30 秒 |
| `lifetime` | `exp` 早于 `iat`，或 `exp - iat` 超过 5 分钟 |
| `missing_claim` | `sub` 或 `jti` 为空或缺失 |
| `request_mismatch` | `req` 缺失，或不等于 `"sha256:" + hex(SHA-256(规范化 JSON {method, params 去掉 _meta}))`（规范化：键排序、无多余空白、不转义 HTML、数字照原样） |
| `replay` | 这个 `jti` 在这个验签方已经被接受过 |

### 用例

| 结果 | 用例 |
|---|---|
| 接受 | `good`；参数换了写法（空白、键序、字符转义）仍然接受；没有角色、客户端、actor；用第二把密钥签；`exp` 之后 30 秒仍在容差内；`iat` 比验签方时钟早 30 秒内；`aud` 是包含自己的列表；有效期正好 5 分钟；断言正好 8192 字节 |
| `no_assertion` | 没有断言（只有没签名的调用者） |
| `malformed` | 不是 JWS；不是 base64url；超过 8192 字节；声明是列表 |
| `header` | `alg` 为 `none`；`HS256` 以公钥 PEM 当密钥；`typ` 为 `JWT`；P-384 密钥签的 `ES384` |
| `unknown_key` | 持有之外的密钥与它自己的 `kid` |
| `bad_signature` | 别的密钥签、用网关的 `kid`；换了声明；签名长度不对；P-384 签名放在 `ES256` 头下 |
| `audience` | 网关为另一个上游签的；`aud` 列表不含自己 |
| `expired` / `issued_in_future` / `lifetime` | 超出容差 1 秒；无 `exp` / 无 `iat`；有效期超过 5 分钟；`exp` 早于 `iat` |
| `missing_claim` | 空 `sub`；空 `jti`；无 `jti` |
| `request_mismatch` | 无 `req`；签名之后改了参数；签的是另一个方法 |
| `replay` | 同一个断言出示两次；同一个 `jti` 用在另一个请求上 |

## 加用例或加规则

1. 先改 idp-mcp 的生成测试（`internal/gateway/caller_vectors_test.go`），再重新生成。
2. 新的拒绝原因要加进上表与生成测试的 `vectorReasons`，idp-sandbox 与 idp-web 的测试在不认识的关键字上会失败，要给它们补上对应的错误。
3. 把新文件复制到四处。
