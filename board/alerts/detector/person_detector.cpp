// Inference uses Tencent ncnn (BSD-3-Clause) and MobileNet-SSD VOC.
#include "net.h"
#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_JPEG
#include "stb_image.h"
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <chrono>
#include <iostream>
#include <string>

int main(int argc,char **argv){
    if(argc!=3){std::fprintf(stderr,"Usage: person_detector model.param model.bin\n");return 2;}
    ncnn::Net net;net.opt.num_threads=1;net.opt.use_vulkan_compute=false;
    net.opt.use_packing_layout=true;net.opt.lightmode=true;
    if(net.load_param(argv[1])||net.load_model(argv[2]))return 3;
    std::string file;
    while(std::getline(std::cin,file)){
        if(!file.empty()&&file.back()=='\r')file.pop_back();
        auto start=std::chrono::steady_clock::now();
        if(file.size()>512||file.empty()){std::puts("{\"ok\":false,\"error\":\"invalid_path\"}");std::fflush(stdout);continue;}
        int w=0,h=0,components=0;unsigned char *pixels=stbi_load(file.c_str(),&w,&h,&components,3);
        if(!pixels||w<16||h<16||w>1920||h>1080){if(pixels)stbi_image_free(pixels);std::puts("{\"ok\":false,\"error\":\"invalid_jpeg\"}");std::fflush(stdout);continue;}
        ncnn::Mat input=ncnn::Mat::from_pixels_resize(pixels,ncnn::Mat::PIXEL_RGB2BGR,w,h,300,300);stbi_image_free(pixels);
        const float mean[3]={127.5f,127.5f,127.5f},norm[3]={1/127.5f,1/127.5f,1/127.5f};
        input.substract_mean_normalize(mean,norm);
        ncnn::Extractor ex=net.create_extractor();ncnn::Mat result;
        if(ex.input("data",input)||ex.extract("detection_out",result)||(!result.empty()&&result.w!=6)){std::puts("{\"ok\":false,\"error\":\"inference_failed\"}");std::fflush(stdout);continue;}
        int count=0;float confidence=0,box[4]={0};
        for(int i=0;i<result.h;i++){
            const float *r=result.row(i);
            if((int)r[0]!=15||!std::isfinite(r[1])||r[1]<0.60f)continue;
            bool finite=true;for(int j=2;j<6;j++)if(!std::isfinite(r[j]))finite=false;
            if(!finite||(r[4]-r[2])*w<16||(r[5]-r[3])*h<32)continue;
            count++;if(r[1]>confidence){confidence=r[1];for(int j=0;j<4;j++)box[j]=std::fmax(0.0f,std::fmin(1.0f,r[j+2]));}
        }
        long ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
        std::printf("{\"ok\":true,\"person\":%s,\"count\":%d,\"confidence\":%.4f,\"bbox\":[%.4f,%.4f,%.4f,%.4f],\"inference_ms\":%ld}\n",count?"true":"false",count,confidence,box[0],box[1],box[2],box[3],ms);std::fflush(stdout);
    }
    return 0;
}
