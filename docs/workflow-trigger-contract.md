# 워크플로우 공통 트리거 계약

## 1. 기본 구조

워크플로우 규칙은 **언제 발생했는가(트리거), 현재 실행 조건을 충족하는가(조건), 무엇을 실행하는가(액션)**로 구성한다. 봇과 내장 효과(기존 화면의 스펠)는 액션 구현체다.

현재 단계 정책의 완료 집계·활성 대기열·기한 초과 정책은 정적 상태 의미이며, 이벤트 액션과 분리한다.

## 2. Rules and execution history

Each rule has one trigger mode: stage entry, a supported event, or an interval.
Conditions and actions are ordered. Conditions accept supported comparisons and
validated values;
arbitrary code, SQL and recursive expressions are not supported. Action
configuration is specific to the action kind and rejects unknown keys. Bot
references belong to `bot.invoke` actions. Actions can require a preceding action
to succeed.

Execution history records the rule revision, event, board/card, stage-entry
occurrence, actor and condition result. Individual action attempts distinguish
admission from execution success. Historical configuration and existing execution
history remain preserved.

## 3. 트리거와 조건

### 트리거

- **단계 진입**: 서로 다른 단계로 이동이 커밋됐을 때 1회 발생한다. 동일 단계 내 칼럼 이동은 단계 진입이 아니다.
- **이벤트 발생**: 카드 수정·댓글 추가/수정·첨부·체크리스트 등 기존 native 이벤트를 구독한다. 공통 DomainEventType이 유일한 이벤트 종류 정의다. 구 이름 alias와 호환 어댑터는 제공하지 않는다.
- **주기 도래**: 활성 상태인 해당 단계의 카드에 대해 규칙의 인터벌이 도래하면 발생한다. 타이머 이벤트 ID는 규칙·카드·진입 회차·예정 시각으로 안정적으로 결정한다. 재시도마다 새 ID를 만들지 않는다. 누락 구간은 기존 실행기의 합의된 복구 정책으로 처리하며 무제한 따라잡기를 하지 않는다.

이벤트 봉투에는 안정적인 event_id, event_type, occurred_at, actor 참조, project/card 참조, 진입 회차, 원인 이벤트/실행 참조를 둔다. 생성 시점의 원문·비밀·첨부 내용 전체를 실행 이력에 복제하지 않는다. 이벤트와 실행 예약은 원본 변경과 같은 커밋 경계에서 내구성 있게 기록하고, 외부 호출은 커밋 뒤 수행한다.

### 조건

조건은 현재 상태의 판정이며 효과 이름을 포함하지 않는다. 예: 현재 단계 일치, 단계 체류 시간, 미압축 댓글 글자 수/댓글 수/대화 턴 수, 미완료 항목 존재, 타이머·승인·의존성 상태, 발생자와 카드 작성자 차이.

한 규칙 안에서 `모두 충족` 또는 `하나 이상 충족`을 지원한다. 임의 중첩 문법은 도입하지 않는다. 압축의 글자 수·댓글 수·턴 수는 OR 조건이다. 서로 다른 의미의 조건 묶음이 필요하면 별도 규칙으로 표시하고, 액션의 필수 안전 조건은 항상 추가 AND로 평가한다. 0으로 비활성화한 압축 기준은 참으로 취급하지 않고 평가에서 제외한다. 모든 기준이 비활성인 규칙은 저장을 거부한다.

## 4. 액션

Actions:

- 봇 호출: 기존 봇·Hook·카드 예약 참조를 이용한다. 규칙이 맞아도 기존 scope, 이벤트 조건, 동결 상태, 활성 상태를 다시 검사한다. 연결만으로 권한이나 Hook을 생성하지 않는다.
- 댓글 압축: 전용 컴팩터 프로필과 보드별 명시적 grant를 사용한다. 이전 요약과 새 댓글을 입력으로 삼고 원문·근거·키워드/엔티티·ACL 계약을 유지한다.
- 카드 아카이브: 현재 권한·유예·작업·승인·의존성 검사를 유지한다. 압축과 함께 설정된 기존 정책은 압축 성공이 확인돼야 아카이브할 수 있다.
- 체크항목 완료, 실행 중 타이머 정지, 작성자 알림.

후속 액션은 설정된 선행 성공 계약을 따른다. 봇 요청의 accepted는 succeeded로 간주하지 않는다.

## 5. 이동 확인은 사전 조건

`자손도 함께 이동할지 확인`은 이동 후 실행할 액션이 아니다. 이동 명령의 커밋 전 확인 정책이다. 부모 없는 루트 카드·전체 자손·단계 변경 조건을 검사하고 전체 이동/단독 이동/취소를 받는다. 확인 뒤 관계·칼럼·정책 revision을 다시 검사하고 기존 원자적 이동 경로를 사용한다. 취소된 이동에는 단계 진입 이벤트와 효과 실행을 만들지 않는다.

## 6. 실행 주체·중복·재시도

- 이벤트 발생자, 규칙 설정자, 실제 실행 주체는 각각 기록한다. 저장한 사용자를 모든 효과의 자동 실행자로 간주하지 않는다.
- 압축은 컴팩터, 봇 호출은 해당 봇을 주체로 기록한다. 아카이브 등 각 액션의 주체와 보드 권한은 명시적으로 연결하고 실행 직전에 재검사한다. 내부 프로필이라는 이유로 자동 권한을 부여하지 않는다.
- 실행 직전에 카드·보드·규칙 활성 상태, 현재 revision, 단계 진입 회차, 공개 범위, 현재 실행 주체 권한과 액션 고유 조건을 재조회한다. 오래된 예약은 skipped/cancelled 사유를 남긴다.
- 중복 방지의 논리 키는 안정적인 event_id + rule_id + action_id + 단계 진입 회차다. 같은 액션의 재시도는 같은 실행을 참조한다. 규칙 수정은 과거 이벤트를 새 실행으로 재생하지 않는다.
- 이벤트를 재전달해도 새 UUID로 새 실행을 만들지 않는다. 재시도 횟수·다음 시각·오류 분류를 별도 기록한다. 외부 시스템이 수신했는지 불명확한 실행은 uncertain으로 남기고 무조건 재전송하지 않는다. 외부 시스템의 멱등 키 지원 시 같은 키를 전달한다.
- 인터벌 봇의 `accepted`는 해당 회차 전송 접수 기록이다. 같은 회차를 재전송하거나 성공으로 바꾸지 않으며, 다음 설정 회차는 새 발생으로 허용한다. `queued`/`running` 전송과 `uncertain`은 후속 회차를 막는다. 현재 봇 경로에는 원격 실행 완료 통지가 없으므로 다음 회차에서 원격 작업의 동시 실행까지 방지한다고 보장하지 않는다.
- 자동 액션이 만든 이벤트에는 원인 실행을 연결한다. 기본적으로 자기 규칙의 재귀 실행을 막고, 기존 봇 작성 이벤트의 루프 방지 계약을 보존한다. 일반 사용자 이벤트를 전역적으로 억제하지 않는다.

## 7. 설정 화면과 이력

Configure a rule by choosing `발동 시점`, `조건` and `실행할 작업`, in that order.

예시:

1. 발동 시점: 댓글 추가 / 조건: 미압축 글자 수 12,000 이상 **또는** 새 댓글 20개 이상 / 작업: 댓글 압축(컴팩터).
2. 발동 시점: 단계 진입 / 조건: 이동자와 작성자가 다름 / 작업: 작성자 알림.
3. 발동 시점: 5분마다 / 조건: 해당 단계 체류 및 기존 아카이브 안전 조건 / 작업: 압축 → 성공 시 아카이브.
4. 발동 시점: 카드 수정 / 조건: 해당 단계에 머무는 중 / 작업: 태그봇 호출. 태그봇의 기존 이벤트 조건도 충족해야 실행한다.

이력에는 발동 시각·종류, 발생자, 대상 카드, 조건 판정, 규칙 revision, 액션별 실행자·상태·실행 시간·결과 참조를 표시한다. queued/running/accepted/succeeded/failed/skipped/cancelled/uncertain을 구분하며 재시도는 같은 실행의 시도 이력으로 제공한다. 원문 접근 권한과 비밀 마스킹을 그대로 적용한다.

## 8. Upgrade and migration

Legacy event aliases and compatibility adapters are not available after
migration. Upgrade clients to the common trigger, condition and action API.

1. 데이터를 변경하기 전에 현재 설정·이력과 진행 중 작업을 조사한다. 설정과 과거 실행 증거는 보존하며 자동 재실행하지 않는다.
2. The migration converts `entry_effects`, `entry_bots`, `policy_bots` and `continuous_effects` into rules, conditions and actions, while retaining static stage policies and pre-move confirmation policies. Verify counts and references for every existing entry. Stop the migration if a row cannot be represented; do not silently skip rows or overwrite them with defaults.
3. Bot Hook subscriptions use the common event types and retain their scope, permissions, freeze state and subscription conditions. Subscription and permission checks remain prerequisites for execution.
4. Upgrade the server, UI, MCP and SDK clients together. Old requests are rejected with validation errors; no compatibility translation is available.
5. 전환 시 기존 이벤트 생산자와 worker를 정지·배수하고 진행 중/accepted/uncertain 작업을 확인한다. 소유권과 중복 방지 기록을 새 계약으로 옮긴 뒤 새 생산자·실행기만 기동한다. 구·신 실행기의 동시 구동과 구 코드를 재활성화하는 fallback을 금지한다.
6. 재시도 가능한 대기 작업은 원래 이벤트 ID와 실행 키를 보존해 변환한다. 전달 여부가 불명확한 외부 작업을 새 작업으로 재생하지 않는다. 과거 이력은 읽기 전용 증거로 유지하고 새 공통 이력에서 참조한다.
7. Verify populated-database conversion, history preservation and single execution ownership before enabling the new workflow rules. On failure, stop writes and recover from a verified backup or a forward fix. Do not downgrade new rules into lossy legacy fields or run both executors.
