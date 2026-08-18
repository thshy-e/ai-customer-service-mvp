.PHONY: test config prepare up down logs

test:
	.venv/bin/pytest -q ai_service/tests

config:
	docker compose config --quiet

prepare:
	docker compose run --rm rails bundle exec rails db:chatwoot_prepare

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100 rails sidekiq ai-service web

