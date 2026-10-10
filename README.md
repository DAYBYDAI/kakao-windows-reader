# kakao-windows-reader

> 사람용 설명서는 없습니다. Windows PC에서 이 페이지 주소를 Claude Code 같은 AI 코딩 도구에 붙여넣고 **"이거 세팅해 줘"** 라고 말하면 AI가 설치와 설정을 진행합니다.

## AI 에이전트에게

사용자가 이 링크를 주며 세팅을 요청했다면, 이 페이지 요약만 보고 설치하지 말고 아래 순서로 하세요.

0. 먼저 Windows인지 봅니다. Git Bash에서 `uname -s`가 `MINGW…`·`MSYS…`이거나 PowerShell에서 `$env:OS`가 `Windows_NT`면 Windows입니다. 아니면(macOS·Linux·WSL) **아무것도 내려받지 말고** "Windows PC 카카오톡 전용이라 이 컴퓨터에서는 쓸 수 없어요"라고 알리고 끝냅니다.
1. 이 저장소를 사용자 PC의 `%USERPROFILE%\kakao-windows-reader`에 받습니다. 그 폴더가 이미 있으면 `git -C <폴더> pull --ff-only`로 갱신합니다.
   ```powershell
   git clone https://github.com/DAYBYDAI/kakao-windows-reader.git "$env:USERPROFILE\kakao-windows-reader"
   ```
   ```bash
   # Git Bash (Claude Code 기본 셸)
   git clone https://github.com/DAYBYDAI/kakao-windows-reader.git "$(cygpath "$USERPROFILE")/kakao-windows-reader"
   ```
   git이 없으면 ZIP을 받아 풉니다: https://github.com/DAYBYDAI/kakao-windows-reader/archive/refs/heads/main.zip
2. 받은 폴더의 **[AGENTS.md](AGENTS.md)** 를 처음부터 끝까지 읽고 그 순서대로 진행합니다. 원문 주소: https://raw.githubusercontent.com/DAYBYDAI/kakao-windows-reader/main/AGENTS.md

이 도구는 Windows 카카오톡 PC의 로컬 대화 DB를 **읽기만** 합니다. 메시지를 보내지 않고, 리더 자체는 인터넷을 쓰지 않습니다. 비밀번호를 묻지 않고 관리자 권한도 쓰지 않습니다. Windows 전용이며 macOS·WSL·웹 환경에서는 동작하지 않습니다.

---

MIT 라이선스. 키 복구와 DB 복호화 로직은 [is-theo/kakao-cli-win](https://github.com/is-theo/kakao-cli-win)(MIT)을 바탕으로 고쳤습니다. 출처는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 있습니다. 카카오·Anthropic의 공식 도구가 아닙니다.

만든 이: 데바대이(DAYBYDAI) 대표 허세임 (huhsame) · https://huhsame.com
