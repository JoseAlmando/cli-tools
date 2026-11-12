# Jenkins

`JENKINS_URL` is the controller URL, including any context path (for example `https://jenkins.example.com/ci`). A trailing `/api/` is accepted. The owner can save an API token with `clt jenkins login --url URL --user USER` or set `JENKINS_USER` and `JENKINS_TOKEN` alongside `JENKINS_URL`. With no credentials, calls use Jenkins' anonymous permissions. A saved session is used only for its own controller. Plain HTTP requires `JENKINS_ALLOW_HTTP=1` and sends any credentials without TLS; use HTTPS when available.

```bash
clt jenkins --json --read-only me
clt jenkins --json --read-only jobs --table
clt jenkins --json --read-only jobs 'Folder/Subfolder' --table
clt jenkins --json --read-only build 'Folder/Job' lastCompletedBuild
clt jenkins --json --read-only log 'Folder/Job' 42 --tail 200     # the end, where a failure usually is
clt jenkins --json --read-only queue --table
clt jenkins --json --read-only GET 'job/Folder/job/Job/api/json?tree=lastBuild[number,result],builds[number,result]{0,10}'
```

`build` and `log` default to `lastBuild` and accept build numbers and permalinks such as `lastSuccessfulBuild`. `log` writes plain text, even with `--json`. Build output can be long: start with `--tail 200`, and show only the lines relevant to the task. The API is rooted at each resource (`api/json`, `job/.../api/json`, `queue/api/json`); use Jenkins' `tree` parameter for fields and list ranges.
