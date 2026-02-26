FROM node:20-alpine

# Set working directory
WORKDIR /app

# Copy package.json and lock file
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy frontend code
COPY . .

# 1. 声明我们要接收一个构建参数
ARG NEXT_PUBLIC_API_URL
# 2. 把这个参数转化为容器内的环境变量，供 Next.js 打包时读取
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# Build the Next.js app
RUN npm run build

# Expose port
EXPOSE 3000

# Start Next.js server
CMD ["npm", "start"]
