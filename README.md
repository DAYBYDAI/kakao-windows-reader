# kakao-windows-reader

Windows 카카오톡 PC의 **내 계정 대화**를 AI 코딩 도구(Claude Code 등)가 읽게 해 주는 스킬입니다. 읽기만 하고 메시지는 보내지 않습니다.

## 쓰는 법

카카오톡 PC가 깔린 **내 Windows PC의 Claude Code**(터미널, 또는 Claude 데스크톱 앱의 Code 탭 로컬 세션)에 아래 문장을 붙여 넣으세요.

```text
내 PC에서 내 카카오톡을 읽는 스킬을 설치해 줘. 먼저 AGENTS.md 원문을 끝까지 읽고 0단계부터 순서대로 진행해 줘. https://github.com/DAYBYDAI/kakao-windows-reader
```

AI가 단계마다 무엇을 하는지 말하고, 확인이 필요한 곳에서는 물어봅니다. 카카오톡 PC는 켜고 로그인해 두세요.

**claude.ai 웹, 데스크톱 앱의 일반 채팅, Cowork에서는 안 됩니다.** 이런 곳의 AI는 내 PC에서 명령을 실행할 수 없어서 "여기서는 설치할 수 없다"고 답합니다. 맥은 [kakao-mac](https://github.com/DAYBYDAI/kakao-mac)을 쓰세요.

### AI가 설치를 못 해 줄 때

직접 설치할 수도 있습니다. [ZIP 내려받기](https://github.com/DAYBYDAI/kakao-windows-reader/archive/refs/heads/main.zip)로 받아 압축을 풀고 `Install-Claude-Skill.cmd`를 더블클릭하세요(64비트 Python 3.11 이상 필요). 설치가 끝나면 Claude Code를 다시 열고 `/kakao-windows 첫 설정을 해 줘`라고 말하면 됩니다.

## 무엇을 하고 무엇을 안 하나

- 카카오톡 PC가 이 PC에 저장해 둔 대화 DB를 읽기만 합니다. 메시지를 보내는 기능이 없습니다.
- DB가 암호화돼 있어서, 내가 로그인해 켜 둔 카카오톡 프로그램의 메모리에서 키를 찾습니다. 같은 Windows 사용자 권한으로만 읽고, 찾은 키는 지금 Windows 사용자만 풀 수 있게(DPAPI) 저장합니다.
- 비밀번호를 묻지 않고 관리자 권한을 쓰지 않습니다. 리더 자체는 인터넷을 쓰지 않습니다(설치할 때 PyPI에서 암호화 라이브러리 하나를 받습니다).
- 다만 AI에게 대화를 요약·검색시키면 그 내용은 AI 서비스로 전송됩니다.

## AI 에이전트에게

사용자가 이 링크를 주며 세팅을 요청했다면:

1. 이 페이지 요약만 보고 설치하지 않는다. 먼저 **[AGENTS.md](AGENTS.md)** 원문을 처음부터 끝까지 읽는다. 원문 주소: https://raw.githubusercontent.com/DAYBYDAI/kakao-windows-reader/main/AGENTS.md
2. AGENTS.md의 0단계(사용자에게 설명하고 시작 확인받기)부터 순서대로 한다. 저장소 내려받기는 그 확인을 받은 뒤 3단계에서 한다.
3. 지금 환경이 사용자의 Windows PC가 아니면(Git Bash의 `uname -s`가 `MINGW…`·`MSYS…`가 아니고 PowerShell의 `$env:OS`도 `Windows_NT`가 아니면, 또는 클라우드·가상 환경이면) 아무것도 내려받지 말고 "카카오톡 PC가 깔린 Windows PC의 Claude Code에서 같은 링크로 다시 요청해 주세요"라고 알린다.

## 알아 둘 것

- 카카오의 공식 도구가 아닙니다. 카카오톡이 업데이트되면 동작하지 않을 수 있습니다. 검증한 버전은 카카오톡 PC 26.7.1.5263(64비트)입니다.
- 본인에게 접근 권한이 있는 본인 계정의 자료에만 쓰세요. 다른 사람의 대화를 몰래 보는 데 쓰면 안 됩니다. 카카오톡 이용약관과 개인정보·통신비밀 관련 법을 지킬 책임은 쓰는 사람에게 있습니다. 이 문서는 법률 자문이 아닙니다.
- 다른 프로그램의 메모리를 읽는 동작이라 백신이 막을 수 있습니다. 백신을 끄지 말고, 무엇을 하는 도구인지 확인한 뒤 직접 판단하세요.

---

MIT 라이선스. 키 복구와 DB 복호화 로직은 [is-theo/kakao-cli-win](https://github.com/is-theo/kakao-cli-win)(MIT)을 바탕으로 고쳤습니다. 출처는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 있습니다. 카카오·Anthropic과 관계없는 비공식 도구이며, 카카오톡은 해당 권리자의 상표입니다.

만든 이: 데바대이(DAYBYDAI) 대표 허세임 (huhsame) · https://huhsame.com
