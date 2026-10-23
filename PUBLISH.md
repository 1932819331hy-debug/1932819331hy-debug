# 发布三个已验证改动

当前新建上游 PR 数为 0。两个 Chain-Love 分支已推送；mm 已提交并打包。云端官方 CLI 登录已完成，但实际创建 PR 和 fork 仍被 GitHub 拒绝，具体根因尚未确认。无需再次进行云端登录。

以下命令供你正常授权的本地 GitHub CLI 使用，不修改代理或网络控制。下载并解压交付包，进入包含 `precommit-csv-records`、`somnia-multicall3`、`mm-mil2mm-decimal` 三个子目录的目录后，在 Bash 中运行。首个 API 请求核对账号；必须为 `1932819331hy-debug`。命令遇到错误即停止，不修改全局 Git 配置，也不强推。

Somnia 必须创建为 **草稿**：账号持有人的个人来源核验声明尚未确认。其他两个 PR 可直接提交审查。重复运行前先查看已有 PR，跳过已完成的创建步骤。

```bash
(
  set -euo pipefail
  publication_login="$(gh api user --jq '.login')"
  printf 'GitHub account: %s\n' "$publication_login"
  test "$publication_login" = '1932819331hy-debug'
  publication_dir="$(pwd -P)"
  test -f "$publication_dir/precommit-csv-records/proposed-body.md"
  test -f "$publication_dir/somnia-multicall3/proposed-body.md"
  test -f "$publication_dir/mm-mil2mm-decimal/proposed-body.md"
  test -f "$publication_dir/mm-mil2mm-decimal/change.bundle"

  test "$(gh api repos/1932819331hy-debug/chain-love/git/ref/heads/fix/precommit-csv-records --jq '.object.sha')" = '61183af412d1edd0e4f657c68a02e316272d9fad'
  gh pr create --repo Chain-Love/chain-love --base main \
    --head 1932819331hy-debug:fix/precommit-csv-records \
    --title 'fix(hooks): preserve logical CSV records when sorting' \
    --body-file "$publication_dir/precommit-csv-records/proposed-body.md"

  test "$(gh api repos/1932819331hy-debug/chain-love/git/ref/heads/data/somnia-multicall3 --jq '.object.sha')" = '67820f9f2a89f3edd28bd8c40319135d19fdb957'
  gh pr create --repo Chain-Love/chain-love --base main \
    --head 1932819331hy-debug:data/somnia-multicall3 \
    --title 'data(somnia): document Multicall3 service deployments' \
    --body-file "$publication_dir/somnia-multicall3/proposed-body.md" --draft

  gh repo fork tscircuit/mm --clone=false --remote=false
  test "$(gh api repos/1932819331hy-debug/mm --jq '.parent.full_name')" = 'tscircuit/mm'
  publication_tmp="$(mktemp -d "${TMPDIR:-/tmp}/mm-pr.XXXXXX")"
  git -c credential.helper= -c 'credential.helper=!gh auth git-credential' \
    clone https://github.com/1932819331hy-debug/mm.git "$publication_tmp/mm"

  # bundle 需要这个上游基础提交；先取回，再导入已验证分支。
  git -C "$publication_tmp/mm" -c credential.helper= \
    -c 'credential.helper=!gh auth git-credential' \
    fetch https://github.com/tscircuit/mm.git 10ca85f3b39f993d88c2be35c2cc4ed814a2cd43
  git -C "$publication_tmp/mm" bundle verify "$publication_dir/mm-mil2mm-decimal/change.bundle"
  git -C "$publication_tmp/mm" fetch "$publication_dir/mm-mil2mm-decimal/change.bundle" \
    refs/heads/fix/mil2mm-decimal-strings:refs/heads/fix/mil2mm-decimal-strings
  test "$(git -C "$publication_tmp/mm" rev-parse refs/heads/fix/mil2mm-decimal-strings)" = 'fa997a5cdfabd854c4007e80a14a2869a7d0152c'
  git -C "$publication_tmp/mm" -c credential.helper= \
    -c 'credential.helper=!gh auth git-credential' push origin \
    refs/heads/fix/mil2mm-decimal-strings:refs/heads/fix/mil2mm-decimal-strings
  test "$(gh api repos/1932819331hy-debug/mm/git/ref/heads/fix/mil2mm-decimal-strings --jq '.object.sha')" = 'fa997a5cdfabd854c4007e80a14a2869a7d0152c'

  gh pr create --repo tscircuit/mm --base main \
    --head 1932819331hy-debug:fix/mil2mm-decimal-strings \
    --title 'fix: interpret signed decimal mil strings consistently' \
    --body-file "$publication_dir/mm-mil2mm-decimal/proposed-body.md"
)
```

也可在已登录的浏览器中使用现有 Chain-Love 分支的预填页面：

- [CSV 排序修复：已填标题和正文](https://github.com/Chain-Love/chain-love/compare/main...1932819331hy-debug:fix/precommit-csv-records?expand=1&title=fix%28hooks%29%3A+preserve+logical+CSV+records+when+sorting&body=A+valid+CSV+field+containing+a+newline+is+currently+split+into+separate+records+by+the+pre-commit+sort.+For+example%2C+%60z%2C%22first%5Cnlast%22%60+and+%60a%2Cplain%60+become+three+parsed+records+and+the+original+field+is+truncated.+A+quoted+delimiter+before+a+non-leading+slug+column+also+produces+the+wrong+sort+key.%0A%0AUse+Python%27s+CSV+parser+to+identify+logical+record+boundaries+and+slug+values%2C+while+retaining+each+record%27s+original+source+text.+This+preserves+quoting+and+embedded+line+endings+and+keeps+an+unterminated+final+record+separate+after+reordering.+Malformed+unterminated+CSV+fails+before+that+file+is+rewritten+or+staged.%0A%0AValidation+at+%6061183af412d1edd0e4f657c68a02e316272d9fad%60%3A%0A-+%60python3+-B+-m+unittest+discover+-s+git-hooks%2Ftests+-v%60%3A+12+tests+pass%2C+with+actual+temporary+Git+staging.+Seven+of+the+initial+ten+regressions+fail+against+the+original+sorter.%0A-+An+isolated+archive+of+current+main+contains+959+CSV+files.+Sorting+changes+zero+existing+bytes%2C+and+a+second+sort+is+identical+in+both+the+working+tree+and+Git+index.%0A-+Independent+boundary+probes+passed+1%2C458+combinations+of+LF%2FCRLF%2FCR%2C+blank+records%2C+multiline+fields+and+terminal-newline+presence.%0A-+Python+compilation+and+%60git+diff+--check%60+pass.+The+network-downloading+full+hook+and+hosted+CI+have+not+run.%0A%0ANo+current+main+CSV+contains+a+multiline+record%3B+the+trigger+is+valid+future+contributor+input.+This+change+keeps+the+existing+staging+scope.+Related+PRs+%233827+and+%233995+address+staging+behavior+and+overlap+the+same+function%2C+but+their+inspected+patches+still+parse+physical+lines%3B+maintainer+coordination+may+be+needed+if+either+lands+first.%0A%0AAI+assistance+was+used+for+implementation+and+review.+This+code+change+has+no+confirmed+grant+eligibility%3B+no+database+fields+or+reward+claim+are+included.%0A%0A)
- [Somnia Multicall3：已填标题和正文](https://github.com/Chain-Love/chain-love/compare/main...1932819331hy-debug:data/somnia-multicall3?expand=1&title=data%28somnia%29%3A+document+Multicall3+service+deployments&body=Somnia%27s+service+catalog+currently+omits+Multicall3+even+though+its+official+network+documentation+lists+deployments+for+both+mainnet+and+Shannon+testnet.+This+adds+one+listing+that+reuses+the+existing+%60%21offer%3Amulticall3%60+template%2C+links+the+network+documentation%2C+and+labels+the+two+different+deployment+addresses+explicitly.%0A%0AOnly+one+row+in+%60listings%2Fspecific-networks%2Fsomnia%2Fservices.csv%60+changes.+Existing+records+and+canonical+offers+remain+byte-for-byte+unchanged.%0A%0ASources+checked+on+2026-10-23%3A%0A-+https%3A%2F%2Fdocs.somnia.network%2Fdeveloper%2Fnetwork-info%0A-+https%3A%2F%2Fdocs.somnia.network%2Fdeveloper%2Fsmart-contracts%0A%0AValidation+at+%6067820f9f2a89f3edd28bd8c40319135d19fdb957%60%3A+the+pinned+upstream+%60validate_csv.py%60%2C+%60csv_to_json.py%60+and+%60validate.py%60+checks+all+pass+with+Python+3.12.14+and+json-tools+%60830d5f05ae15954c43ef23f4085c83d4548d5431%60.+CSV+column+count%2C+slug+order+and+unchanged-row+bytes+also+pass.+This+is+local+validation%3B+hosted+CI+has+not+run.%0A%0AThe+deployment+claim+is+based+on+the+official+documentation%3B+no+live+RPC+bytecode+check+is+claimed.+Indexed+open-PR+searches+found+no+equivalent+Somnia+Multicall3+listing%3B+%232879+concerns+Filecoin+and+%234544+adds+WSOMI.%0A%0AAI+assistance+was+used+for+research%2C+preparation+and+validation.+Account-holder+personal-verification+checklist+items+remain+unconfirmed.+Reward+eligibility+and+payment+remain+subject+to+maintainer+review%3B+no+reward+address+has+been+supplied.%0A%0A)。页面上选择 **Create draft pull request**；链接本身不会保证草稿状态。

成功创建后保存实际 PR URL，再用 `gh pr view URL --json url,isDraft,headRefOid,statusCheckRollup` 核对提交和 CI。等待维护者批准运行不等于测试通过。奖励资格和付款均未确认。
