# The PostgreSQL backup image (DEV and reference): postgres client tools plus age (public-key encryption) and
# curl (S3-compatible upload with SigV4: AWS S3, MinIO, Cloudflare R2, Oracle OCI, ...). Nothing else, so there is
# little to patch. Build: deploy/images/pg-backup/build.sh
FROM public.ecr.aws/docker/library/postgres:17-alpine
USER root
# upgrade the base's packages (a fixed musl, for one, lands in a newer package than the image was built with), install the two
# tools, and drop gosu: the official PostgreSQL images ship it built with an old Go (its standard library is what a scanner
# flags), it only serves docker-entrypoint.sh, and this image never runs that entrypoint - it runs backup.sh and restore.sh
RUN apk upgrade --no-cache && apk add --no-cache age curl && rm -f /usr/local/bin/gosu
COPY s3.sh backup.sh restore.sh /usr/local/bin/
RUN chmod 0555 /usr/local/bin/s3.sh /usr/local/bin/backup.sh /usr/local/bin/restore.sh
USER 70:70
