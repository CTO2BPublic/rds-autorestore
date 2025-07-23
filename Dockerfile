FROM public.ecr.aws/lambda/python:3.11
#FROM python:3.11-slim

# Copy function code
COPY app.py ${LAMBDA_TASK_ROOT}

# (Optional) Install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

CMD ["app.handler"]