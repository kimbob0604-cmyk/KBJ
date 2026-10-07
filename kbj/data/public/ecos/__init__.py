"""한국은행 ECOS — 공개 등급은 한국은행 작성 표만(`client.PUBLIC_TABLES`).

공개 입구 `client.EcosClient`, 전송층 `client.EcosTransport`(로그인 등급 타기관 표는
kbj.data.private.ecos_restricted 가 이 전송층을 재사용), 데이터셋 `datasets.DATASETS`.
"""
