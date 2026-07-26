ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY run.sh ./
COPY web ./web

RUN chmod a+x /app/run.sh

CMD ["/app/run.sh"]
