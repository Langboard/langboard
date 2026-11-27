FROM ubuntu:24.04

RUN apt update && apt install -y gnupg wget curl apt-transport-https lsb-release cron dos2unix

RUN install -d /usr/share/postgresql-common/pgdg && \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc && \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list

RUN apt update && apt install -y postgresql-client-17
RUN rm -rf /var/lib/apt/lists/*

COPY cron.tab /etc/cron.d/cron.tab
RUN dos2unix /etc/cron.d/cron.tab
RUN chmod 0644 /etc/cron.d/cron.tab
RUN mkdir -p /var/log/cron
RUN touch /var/log/cron/backup.log

RUN crontab /etc/cron.d/cron.tab

CMD cron && tail -f /var/log/cron/backup.log
