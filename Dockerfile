ARG ENV=dev
# Use official base image
FROM python:3.10-slim

ENV ENVIRONMENT=$ENV



# Set working directory inside container
WORKDIR /app

# Copy app code
COPY . .

# Install dependencies
RUN pip install -r torch-requirements.txt
RUN pip install -r requirements.txt
RUN pip install boto3
RUN pip install pydantic  
RUN apt-get update && apt-get install -y libgl1
RUN apt-get update && apt-get install -y libglib2.0-0
# Expose port
EXPOSE 8080

# Start the app
CMD ["python3.10" ,"app.py"]
